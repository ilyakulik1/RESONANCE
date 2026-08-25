import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, QEvent, QPoint
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QKeyEvent, QAction
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtWidgets import (
    QApplication, QMainWindow,
    QFileDialog, QMessageBox, QAbstractItemView, QWidget,
    QProgressBar, QLabel, QMenu,
)

from app.audio_devices import (
    combo_selected_device_id,
    device_id_from_config,
    device_id_to_config,
    find_audio_output,
    is_no_output_device,
    populate_audio_output_combo,
    refresh_audio_output_combos,
)
from app.audio_utils import is_audio_file
from app.bpm_detector import BpmDetector
from app.config import get_config_path
from app.constants import APP_NAME, MAX_PLAYLISTS
from app.eq_state import EqState
from app.pcm_air_player import PcmAirPlayer
from app.ui.about_dialog import show_about_dialog
from app.ui.main_window_ui import MainWindowUi
from app.ui.tokens import get_token, get_token_int
from app.ui.icon_loader import icon_size, load_icon
from app.duration_prober import DurationProber
from app.volume_fader import VolumeFader
from app.playlist_io import (
    TRACK_COLOR_PRESETS,
    get_item_bpm,
    get_item_color,
    get_item_duration,
    get_item_file_path,
    load_playlist_from_entries,
    load_playlist_from_file,
    save_playlist_to_file,
    set_item_bpm,
    set_item_color,
    set_item_duration,
)
from app.project import (
    ProjectManager,
    ProjectPlaylistState,
    ProjectState,
    default_project_root,
)
from app.time_utils import format_time_ms, get_audio_duration_ms
from app.waveform_cache import WaveformCache, WaveformCacheEntry, WaveformCacheKey
from app.waveform_loader import WaveformLoader
from app.file_analyzer import FileAnalyzer
from app.analysis_cache import load_peaks, load_bpm
from app.widgets.audio_waveform import AudioWaveform, TimelineState


from app.widgets.playlist_widget import PlaylistWidget


def _peek_saved_project_root() -> Path:
    config_path = get_config_path("config.json")
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            root = config.get("project_path")
            if isinstance(root, str) and os.path.isdir(root):
                return Path(root)
        except Exception:
            pass
    return default_project_root()


class AudioPlayer(QMainWindow):
    @property
    def player(self):
        return self._players[self._active_player_idx]

    @property
    def audio_output(self):
        return self._outputs[self._active_player_idx]

    def __init__(self):
        super().__init__()
        self.playback_mode = "next"
        self.current_playing_playlist = None
        self.current_playing_item = None
        self._current_file_path = None
        self._playback_volume = 0.7
        self._output_volume = 0.7
        self._players = [PcmAirPlayer(self), PcmAirPlayer(self)]
        self._outputs = [p.audioOutput() for p in self._players]
        self._active_player_idx = 0
        self._outputs[1].setVolume(0.0)

        self.config_path = get_config_path("config.json")
        self.playlist_paths = [
            get_config_path(f"playlist{i}.json") for i in range(1, MAX_PLAYLISTS + 1)
        ]
        self.project = ProjectManager(_peek_saved_project_root())
        self._pending_project_root = str(self.project.root)
        self._project_video_mixer: dict | None = None

        self.last_selected_playlist = None
        self._is_window_resizing = False
        self._stable_window_size = None
        self._track_timeline_state: dict[str, TimelineState] = {}
        self._track_eq_state: dict[str, dict] = {}
        self._eq_bound_path: str | None = None
        self._eq_loading = False
        self._fade_out_started = False
        self._handling_range_end = False
        self._range_end_latched = False
        # Ignore EOF/range-end while Space (or other) takeover is in progress.
        self._suppress_range_end = False
        self._pending_space_restart: dict | None = None
        self._stop_fade_active = False
        self._stop_fade_path: str | None = None
        self._stop_fade_item = None
        self._stop_fade_playlist_num: int | None = None
        self._queued_after_stop_fade: dict | None = None
        self._time_display_mode = "elapsed"
        self._pending_playback_start = False
        self._pending_start_ms: int | None = None
        self._seek_target_ms: int | None = None
        self._playback_token = 0
        self._pending_playback_token = 0
        self._displayed_track_path: str | None = None
        self._waveform_request_id = 0
        self._waveform_cache = WaveformCache()
        self._last_playlist_dialog_dir = ""
        self._playlist_titles: dict[int, str] = {}
        self._fade_mode = "sequential"
        self._waveform_res_enabled = False
        self._waveform_was_zoomed = False
        self._pending_transition_fade_ms = 0
        self._crossfade_incoming_path: str | None = None
        self._crossfade_outgoing_idx: int | None = None
        self._crossfade_incoming_idx: int | None = None
        self._pending_playback_player_idx: int | None = None
        self._crossfade_active = False
        self._crossfade_fade_in_ms = 0
        self._crossfade_incoming_ready_to_play = False

        self._preview_player = QMediaPlayer(self)
        self._preview_output = QAudioOutput(self)
        self._preview_player.setAudioOutput(self._preview_output)
        self._preview_playback_volume = 0.7
        self._preview_file_path: str | None = None
        self._preview_displayed_track_path: str | None = None
        self._preview_playlist_num: int | None = None
        self._preview_item = None
        self._preview_waveform_request_id = 0
        self._preview_waveform_res_enabled = False
        self._preview_waveform_was_zoomed = False
        self._preview_pending_playback_start = False
        self._preview_pending_start_ms: int | None = None
        self._preview_seek_target_ms: int | None = None
        self._preview_playback_token = 0
        self._preview_pending_playback_token = 0
        self._preview_fade_out_started = False
        self._preview_handling_range_end = False
        self._preview_range_end_latched = False
        self._preview_autoplay = True
        self._air_output_device_id: bytes | None = None
        self._preview_output_device_id: bytes | None = None
        self._air_no_output = False
        self._preview_no_output = False
        self._browser_no_output = False

        self._browser_player = QMediaPlayer(self)
        self._browser_output = QAudioOutput(self)
        self._browser_player.setAudioOutput(self._browser_output)
        self._browser_playback_volume = 0.7
        self._browser_file_path: str | None = None
        self._browser_displayed_track_path: str | None = None
        self._browser_autoplay = True
        self._browser_output_device_id: bytes | None = None
        self._browser_waveform_request_id = 0
        self._browser_pending_playback_start = False
        self._browser_pending_start_ms: int | None = None
        self._browser_seek_target_ms: int | None = None
        self._browser_playback_token = 0
        self._browser_pending_playback_token = 0
        self._browser_collapsed = False
        self._browser_saved_splitter: list[int] | None = None
        self._properties_collapsed = False
        self._retain_playlist_focus_num: int | None = None
        self._routing_playlist_nav_key = False
        self._spectrum_enabled = True
        self._spectrum_smooth = None
        self._spectrum_timer: QTimer | None = None

        self.setup_ui()
        self._setup_air_spectrum()
        self._setup_audio_output_devices()
        self._update_fade_mode_button()
        self.duration_prober = DurationProber(self)
        self.duration_prober.itemProbed.connect(self._on_playlist_item_probed)
        self.bpm_detector = BpmDetector(self)
        self.bpm_detector.bpm_detected.connect(self._on_bpm_detected)
        self.bpm_detector.item_bpm_updated.connect(self._on_item_bpm_updated)
        self.file_analyzer = FileAnalyzer(self)
        self.file_analyzer.item_analyzed.connect(self._on_item_analyzed)
        self.file_analyzer.file_analyzed.connect(self._on_file_analyzed)
        self.file_analyzer.progress.connect(self._on_analysis_progress)
        self.file_analyzer.busyChanged.connect(self._on_analysis_busy)
        self.file_analyzer.statusChanged.connect(self._on_analysis_status)
        self._setup_analysis_status_ui()
        self.waveform_loader = WaveformLoader(self)
        self.waveform_loader.waveform_ready.connect(self._on_waveform_ready)
        self.preview_waveform_loader = WaveformLoader(self)
        self.preview_waveform_loader.waveform_ready.connect(self._on_preview_waveform_ready)
        self.browser_waveform_loader = WaveformLoader(self)
        self.browser_waveform_loader.waveform_ready.connect(self._on_browser_waveform_ready)
        self.volume_fader = VolumeFader(self._apply_output_volume, self)
        self._preview_volume_fader = VolumeFader(self._apply_preview_output_volume, self)
        self._outgoing_fader = VolumeFader(self._apply_outgoing_volume, self)
        self._incoming_fader = VolumeFader(self._apply_incoming_volume, self)
        self._crossfade_timer = QTimer(self)
        self._crossfade_timer.setSingleShot(True)
        self._crossfade_timer.timeout.connect(self._on_crossfade_switch_timeout)
        self._res_waveform_timer = QTimer(self)
        self._res_waveform_timer.setSingleShot(True)
        self._res_waveform_timer.timeout.connect(self._refresh_res_waveform)
        self._preview_res_waveform_timer = QTimer(self)
        self._preview_res_waveform_timer.setSingleShot(True)
        self._preview_res_waveform_timer.timeout.connect(self._refresh_preview_res_waveform)
        self._preview_player.positionChanged.connect(self._on_preview_position_changed)
        self._preview_player.durationChanged.connect(self._on_preview_duration_changed)
        self._preview_player.mediaStatusChanged.connect(self._on_preview_media_status_changed)
        self._preview_player.playbackStateChanged.connect(self._on_preview_playback_state_changed)
        self._browser_player.positionChanged.connect(self._on_browser_position_changed)
        self._browser_player.durationChanged.connect(self._on_browser_duration_changed)
        self._browser_player.mediaStatusChanged.connect(self._on_browser_media_status_changed)
        self._browser_player.playbackStateChanged.connect(self._on_browser_playback_state_changed)
        for playlist in self.playlists:
            playlist.duration_prober = self.duration_prober
            playlist.set_playlist_registry(self.playlists)
            playlist.itemDropped.connect(self._on_playlist_item_dropped)
            playlist.tracksChanged.connect(
                lambda pl=playlist: self.refresh_playlist_footer(pl)
            )
        self.setup_connections()
        self.setup_shortcuts()
        self._setup_menus()
        self.setWindowTitle(self._window_title())
        self.setMinimumSize(1100, 900)
        self.resize(1280, 1150)
        self.load_project()
        self.load_config()
        self._set_output_volume(self._playback_volume)
        self._set_preview_output_volume(self._preview_playback_volume)
        self._apply_browser_output_volume(self._browser_playback_volume)
        self.refresh_playlist_display()
        self._update_window_title()

        for playlist in self.playlists:
            playlist.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            playlist.customContextMenuRequested.connect(
                lambda pos, pl=playlist: self._show_playlist_context_menu(pl, pos)
            )

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_position)
        self.timer.start(16)

        self._resize_finish_timer = QTimer(self)
        self._resize_finish_timer.setSingleShot(True)
        self._resize_finish_timer.timeout.connect(self._finish_window_resize)

        self.setAcceptDrops(True)
        self.update_time_display()

    def _apply_player_volume(self, idx: int, volume: float) -> None:
        volume = max(0.0, min(1.0, volume))
        self._outputs[idx].setVolume(volume)
        self._outputs[idx].setMuted(self._air_no_output)

    def _apply_preview_output_volume(self, volume: float) -> None:
        volume = max(0.0, min(1.0, volume))
        self._preview_output.setVolume(volume)
        self._preview_output.setMuted(self._preview_no_output)

    def _set_preview_output_volume(self, volume: float) -> None:
        self._preview_volume_fader.sync_volume(volume)
        self._apply_preview_output_volume(volume)

    def _apply_browser_output_volume(self, volume: float) -> None:
        volume = max(0.0, min(1.0, volume))
        self._browser_output.setVolume(volume)
        self._browser_output.setMuted(self._browser_no_output)

    def _setup_audio_output_devices(self) -> None:
        populate_audio_output_combo(
            self.air_output_combo,
            saved_device_id=self._air_output_device_id,
        )
        populate_audio_output_combo(
            self.preview_output_combo,
            saved_device_id=self._preview_output_device_id,
        )
        populate_audio_output_combo(
            self.browser_output_combo,
            saved_device_id=self._browser_output_device_id,
        )
        self.air_output_combo.currentIndexChanged.connect(self._on_air_output_device_changed)
        self.preview_output_combo.currentIndexChanged.connect(
            self._on_preview_output_device_changed
        )
        self.browser_output_combo.currentIndexChanged.connect(
            self._on_browser_output_device_changed
        )
        self._apply_air_output_from_id(combo_selected_device_id(self.air_output_combo))
        self._apply_preview_output_from_id(combo_selected_device_id(self.preview_output_combo))
        self._apply_browser_output_from_id(combo_selected_device_id(self.browser_output_combo))

    def refresh_audio_output_devices(self) -> None:
        """Re-scan system audio outputs and refresh all device combos."""
        combos = []
        for name in ("air_output_combo", "preview_output_combo", "browser_output_combo"):
            combo = getattr(self, name, None)
            if combo is not None:
                combos.append(combo)
        if not combos:
            return
        refresh_audio_output_combos(*combos)
        self._air_output_device_id = combo_selected_device_id(self.air_output_combo)
        self._preview_output_device_id = combo_selected_device_id(self.preview_output_combo)
        self._browser_output_device_id = combo_selected_device_id(self.browser_output_combo)
        self._apply_air_output_from_id(self._air_output_device_id)
        self._apply_preview_output_from_id(self._preview_output_device_id)
        self._apply_browser_output_from_id(self._browser_output_device_id)

    def _apply_air_output_from_id(self, device_id: bytes | None) -> None:
        self._air_no_output = is_no_output_device(device_id)
        if self._air_no_output:
            for output in self._outputs:
                output.setMuted(True)
            return
        device = find_audio_output(device_id)
        for output in self._outputs:
            output.setDevice(device)
            output.setMuted(False)
        if hasattr(self, "volume_fader"):
            self._reapply_output_volume()
        else:
            for output in self._outputs:
                output.setVolume(self._playback_volume)

    def _apply_preview_output_from_id(self, device_id: bytes | None) -> None:
        self._preview_no_output = is_no_output_device(device_id)
        if self._preview_no_output:
            self._preview_output.setMuted(True)
            return
        self._preview_output.setDevice(find_audio_output(device_id))
        self._preview_output.setMuted(False)
        if hasattr(self, "_preview_volume_fader"):
            volume = (
                self._preview_volume_fader.current_volume
                if self._preview_volume_fader.is_active
                else self._preview_playback_volume
            )
        else:
            volume = self._preview_playback_volume
        self._apply_preview_output_volume(volume)

    def _apply_browser_output_from_id(self, device_id: bytes | None) -> None:
        self._browser_no_output = is_no_output_device(device_id)
        if self._browser_no_output:
            self._browser_output.setMuted(True)
            return
        self._browser_output.setDevice(find_audio_output(device_id))
        self._browser_output.setMuted(False)
        self._apply_browser_output_volume(self._browser_playback_volume)

    def _on_air_output_device_changed(self, _index: int) -> None:
        device_id = combo_selected_device_id(self.air_output_combo)
        self._air_output_device_id = device_id
        self._apply_air_output_from_id(device_id)

    def _on_preview_output_device_changed(self, _index: int) -> None:
        device_id = combo_selected_device_id(self.preview_output_combo)
        self._preview_output_device_id = device_id
        self._apply_preview_output_from_id(device_id)

    def _on_browser_output_device_changed(self, _index: int) -> None:
        device_id = combo_selected_device_id(self.browser_output_combo)
        self._browser_output_device_id = device_id
        self._apply_browser_output_from_id(device_id)

    def _apply_outgoing_volume(self, volume: float) -> None:
        idx = self._crossfade_outgoing_idx
        if idx is not None:
            self._apply_player_volume(idx, volume)

    def _apply_incoming_volume(self, volume: float) -> None:
        idx = self._crossfade_incoming_idx
        if idx is not None:
            self._apply_player_volume(idx, volume)

    def _apply_output_volume(self, volume: float) -> None:
        volume = max(0.0, min(1.0, volume))
        self._output_volume = volume
        if self._crossfade_active:
            return
        self._apply_player_volume(self._active_player_idx, volume)

    def _set_output_volume(self, volume: float) -> None:
        self.volume_fader.sync_volume(volume)
        self._apply_output_volume(volume)

    def _reapply_output_volume(self) -> None:
        if self._crossfade_active:
            return
        if self.volume_fader.is_active:
            self._apply_output_volume(self.volume_fader.current_volume)
        else:
            self._apply_output_volume(self._output_volume)

    def setup_shortcuts(self):
        QApplication.instance().installEventFilter(self)

    def _is_editing_playlist(self) -> bool:
        for playlist in self.playlists:
            if playlist.state() == QAbstractItemView.State.EditingState:
                return True
        return False

    def eventFilter(self, watched, event):
        if getattr(self, "_routing_playlist_nav_key", False):
            return super().eventFilter(watched, event)

        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and self.isActiveWindow()
            and not self._is_editing_playlist()
        ):
            if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
                if event.modifiers() & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.AltModifier
                    | Qt.KeyboardModifier.MetaModifier
                    | Qt.KeyboardModifier.ShiftModifier
                ):
                    return False
                self.handle_space()
                return True

            if event.key() == Qt.Key.Key_Escape and not event.isAutoRepeat():
                self.stop()
                return True

            # Even if Qt focus was stolen during preview load, keep ↑/↓ on the
            # playlist that the user is navigating.
            if (
                self._retain_playlist_focus_num is not None
                and self._is_playlist_nav_key(event.key())
            ):
                playlist = self.get_playlist(self._retain_playlist_focus_num)
                focused = QApplication.focusWidget()
                if (
                    playlist is not None
                    and playlist.isVisible()
                    and self._focus_allows_playlist_steal(focused)
                    and not self._widget_belongs_to(watched, playlist)
                    and not self._widget_belongs_to(focused, playlist)
                ):
                    # Call keyPressEvent directly — sendEvent re-enters this
                    # filter and can recurse (esp. when watched is QWindow).
                    self._routing_playlist_nav_key = True
                    try:
                        playlist.setFocus(Qt.FocusReason.OtherFocusReason)
                        playlist.keyPressEvent(event)
                    finally:
                        self._routing_playlist_nav_key = False
                    return True

        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        pos = event.position().toPoint()
        widget_at_pos = self.childAt(pos)

        target_playlist = None
        while widget_at_pos:
            if isinstance(widget_at_pos, PlaylistWidget):
                target_playlist = widget_at_pos
                break
            widget_at_pos = widget_at_pos.parentWidget()

        if not target_playlist:
            target_playlist = self.playlists[0]

        insert_row = target_playlist.drop_insert_row()
        target_playlist._clear_drop_indicator()
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if is_audio_file(file_path):
                target_playlist.add_file(file_path, row=insert_row)
                insert_row += 1
        self.refresh_playlist_footer(target_playlist)
        event.acceptProposedAction()

    def setup_ui(self):
        MainWindowUi().setup_ui(self)

    def _reset_bpm_label(self) -> None:
        self.bpm_label.setText("—")

    def _set_bpm_label_detecting(self) -> None:
        self.bpm_label.setText("…")

    def _setup_analysis_status_ui(self) -> None:
        bar = self.statusBar()
        bar.setObjectName("analysisStatusBar")
        self._analysis_status_label = QLabel("")
        self._analysis_status_label.setObjectName("analysisStatusLabel")
        self._analysis_progress = QProgressBar()
        self._analysis_progress.setObjectName("analysisProgressBar")
        self._analysis_progress.setMinimumWidth(140)
        self._analysis_progress.setMaximumWidth(220)
        self._analysis_progress.setFixedHeight(14)
        self._analysis_progress.setTextVisible(True)
        self._analysis_progress.setFormat("%v / %m")
        self._analysis_progress.hide()
        self._analysis_status_label.hide()
        bar.addWidget(self._analysis_status_label, 1)
        bar.addPermanentWidget(self._analysis_progress)

    def _on_analysis_progress(self, done: int, total: int) -> None:
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        self._analysis_progress.setMaximum(total)
        self._analysis_progress.setValue(done)
        if not self._analysis_progress.isVisible():
            self._analysis_progress.show()

    def _on_analysis_busy(self, busy: bool) -> None:
        if busy:
            self._analysis_progress.show()
            self._analysis_status_label.show()
        else:
            # Keep final message briefly, then clear
            QTimer.singleShot(2500, self._clear_analysis_status_if_idle)

    def _on_analysis_status(self, message: str) -> None:
        self._analysis_status_label.setText(message)
        self._analysis_status_label.show()
        self.statusBar().showMessage(message, 0)

    def _clear_analysis_status_if_idle(self) -> None:
        if self.file_analyzer.is_busy:
            return
        self._analysis_progress.hide()
        self._analysis_progress.setValue(0)
        self._analysis_status_label.hide()
        self._analysis_status_label.clear()
        self.statusBar().clearMessage()

    def _set_bpm_label_value(self, bpm: float) -> None:
        if bpm == int(bpm):
            self.bpm_label.setText(f"{int(bpm)}")
        else:
            self.bpm_label.setText(f"{bpm:.1f}")

    def _setup_air_spectrum(self) -> None:
        """Poll post-EQ spectrum bins from the PCM air player (no second PyAV reader)."""
        for player in self._players:
            player.set_spectrum_enabled(True)
        self._spectrum_timer = QTimer(self)
        self._spectrum_timer.setInterval(80)
        self._spectrum_timer.timeout.connect(self._poll_air_spectrum)
        self._spectrum_timer.start()

    def _clear_air_spectrum(self) -> None:
        self._spectrum_smooth = None
        if hasattr(self, "file_properties_panel"):
            self.file_properties_panel.set_spectrum(None, None)

    def _poll_air_spectrum(self) -> None:
        if not hasattr(self, "file_properties_panel"):
            return
        if not self.file_properties_panel.isVisible():
            return
        if self.file_properties_panel.is_bypassed():
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            if self._spectrum_smooth is not None:
                self._clear_air_spectrum()
            return
        snap = self.player.spectrum_bins_snapshot()
        if snap is None:
            return
        freqs, db = snap
        try:
            import numpy as np

            if self._spectrum_smooth is None or self._spectrum_smooth.shape != db.shape:
                self._spectrum_smooth = db
            else:
                rising = db > self._spectrum_smooth
                alpha = np.where(rising, 0.45, 0.2)
                self._spectrum_smooth = self._spectrum_smooth + alpha * (db - self._spectrum_smooth)
            self.file_properties_panel.set_spectrum(freqs, self._spectrum_smooth)
        except Exception:
            pass

    def _update_file_properties(self, file_path: str | None = None) -> None:
        """Bind air EQ to the current on-air track (metadata lives on preview)."""
        path = file_path or self._current_file_path
        self._bind_eq_to_path(path)

    def _update_preview_properties(self, file_path: str | None = None) -> None:
        if not hasattr(self, "preview_properties_panel"):
            return
        path = self._preview_file_path if file_path is None else file_path
        bpm = None
        if self._preview_item is not None:
            bpm = get_item_bpm(self._preview_item)
        self.preview_properties_panel.set_file(path, bpm=bpm)

    def _eq_path_key(self, file_path: str | None = None) -> str | None:
        path = file_path or self._current_file_path or self._displayed_track_path
        if not path:
            return None
        return os.path.abspath(path)

    def _save_eq_for_bound_path(self) -> None:
        if not hasattr(self, "file_properties_panel") or self._eq_loading:
            return
        key = self._eq_bound_path
        if not key:
            return
        data = self.file_properties_panel.eq_state().to_dict()
        if not data.get("bands") and not data.get("bypass"):
            self._track_eq_state.pop(key, None)
        else:
            self._track_eq_state[key] = data

    def _bind_eq_to_path(self, file_path: str | None) -> None:
        if not hasattr(self, "file_properties_panel"):
            return
        new_key = self._eq_path_key(file_path) if file_path else None
        if new_key == self._eq_bound_path and file_path:
            return
        self._save_eq_for_bound_path()
        self._eq_bound_path = new_key
        self._eq_loading = True
        try:
            if new_key and new_key in self._track_eq_state:
                state = EqState.from_dict(self._track_eq_state[new_key])
            else:
                state = EqState()
            self.file_properties_panel.set_eq_state(state)
        finally:
            self._eq_loading = False
        self._apply_live_eq_to_air()

    def _sync_spectrum_playback(self) -> None:
        # Spectrum follows PCM air playback automatically; nothing to push.
        return

    def _eq_state_for_path(self, file_path: str | None) -> EqState:
        if not file_path:
            return EqState()
        key = os.path.abspath(file_path)
        data = self._track_eq_state.get(key)
        return EqState.from_dict(data) if isinstance(data, dict) else EqState()

    def _set_air_source(self, player_idx: int, file_path: str | None) -> None:
        player = self._players[player_idx]
        if not file_path:
            player.setSource(QUrl())
            return
        player.set_eq_state(self._eq_state_for_path(file_path))
        player.setSource(QUrl.fromLocalFile(file_path))

    def _apply_live_eq_to_air(self) -> None:
        """Push current EQ coefficients to air player(s) that play the bound track.

        Never stomp the still-fading outgoing deck when the UI has already
        switched to the incoming track's EQ during a Space/crossfade takeover.
        """
        if not hasattr(self, "file_properties_panel"):
            return
        state = self.file_properties_panel.eq_state()
        key = self._eq_bound_path
        applied = False
        for idx, player in enumerate(self._players):
            try:
                src = player.source().toLocalFile()
            except Exception:
                continue
            if not src:
                continue
            src_abs = os.path.abspath(src)
            if key is not None:
                if src_abs == key:
                    player.set_eq_state(state)
                    applied = True
            elif idx == self._active_player_idx:
                player.set_eq_state(state)
                applied = True
        if applied:
            return
        # Bound path not loaded yet. During takeover the active deck may still
        # be the previous track — leave its EQ alone; incoming gets EQ via
        # _set_air_source when the source is set.
        if (
            self._crossfade_active
            or self._suppress_range_end
            or self._pending_playback_start
        ):
            return
        try:
            self.player.set_eq_state(state)
        except Exception:
            pass

    def _on_eq_changed(self) -> None:
        if self._eq_loading:
            return
        self._save_eq_for_bound_path()
        # Live DSP first — must be immediate, not debounced.
        self._apply_live_eq_to_air()
        if not hasattr(self, "_eq_save_timer"):
            self._eq_save_timer = QTimer(self)
            self._eq_save_timer.setSingleShot(True)
            self._eq_save_timer.timeout.connect(self._persist_eq_changes)
        self._eq_save_timer.start(400)

    def _persist_eq_changes(self) -> None:
        self._save_eq_for_bound_path()
        self.save_config()

    def collapse_file_properties(self) -> None:
        if self._properties_collapsed:
            return
        self._properties_collapsed = True
        self._apply_properties_collapsed_state(True)
        self.save_config()

    def expand_file_properties(self) -> None:
        if not self._properties_collapsed:
            return
        self._properties_collapsed = False
        self._apply_properties_collapsed_state(False)
        self.save_config()

    def _apply_properties_collapsed_state(self, collapsed: bool) -> None:
        if not hasattr(self, "preview_properties_panel"):
            return
        self.preview_properties_panel.setVisible(not collapsed)
        self._sync_view_menu_actions()

    def _request_bpm_detection(self, file_path: str) -> None:
        item = self.current_playing_item
        if item:
            stored_bpm = get_item_bpm(item)
            if stored_bpm is not None:
                self._set_bpm_label_value(stored_bpm)
                return
        cached_bpm = load_bpm(file_path)
        if cached_bpm is not None:
            self._set_bpm_label_value(cached_bpm)
            if item:
                set_item_bpm(item, cached_bpm)
                self._save_playlist_for_item(item)
            return
        self._set_bpm_label_detecting()
        self.bpm_detector.request_bpm(file_path)

    def _save_playlist_for_item(self, item) -> None:
        widget = item.listWidget()
        if not isinstance(widget, PlaylistWidget):
            return
        try:
            playlist_index = self.playlists.index(widget)
        except ValueError:
            return
        save_playlist_to_file(
            widget,
            str(self.playlist_paths[playlist_index]),
            self._track_timeline_state,
        )
        try:
            self.project.save_raw(self._build_project_state())
        except Exception as exc:
            print(f"Project auto-save error: {exc}")

    def _on_bpm_detected(self, file_path: str, bpm: object) -> None:
        current_abs = os.path.abspath(self._current_file_path) if self._current_file_path else None
        if current_abs != file_path:
            return
        if isinstance(bpm, (int, float)):
            bpm_value = float(bpm)
            self._set_bpm_label_value(bpm_value)
            if self.current_playing_item:
                set_item_bpm(self.current_playing_item, bpm_value)
                self._save_playlist_for_item(self.current_playing_item)
        else:
            self.bpm_label.setText("?")

    def _on_item_bpm_updated(self, item, file_path: str, bpm: object) -> None:
        widget = item.listWidget()
        if isinstance(bpm, (int, float)):
            set_item_bpm(item, float(bpm))
            if widget is not None:
                widget.viewport().update()
            self._save_playlist_for_item(item)
            current_abs = os.path.abspath(self._current_file_path) if self._current_file_path else None
            if (
                self.current_playing_item is item
                and current_abs == os.path.abspath(file_path)
            ):
                self._set_bpm_label_value(float(bpm))
        elif widget is not None:
            widget.viewport().update()
            current_abs = os.path.abspath(self._current_file_path) if self._current_file_path else None
            if self.current_playing_item is item and current_abs == os.path.abspath(file_path):
                self.bpm_label.setText("?")

    def analyze_playlist_files(self, playlist: PlaylistWidget) -> None:
        if playlist.count() == 0:
            QMessageBox.information(self, "Analyze", "Playlist is empty.")
            return
        queued = self.file_analyzer.analyze_playlist(playlist, only_missing=True)
        if queued == 0:
            QMessageBox.information(
                self,
                "Analyze",
                "All tracks already have BPM and cached waveforms.",
            )

    def analyze_selected_file(self, playlist: PlaylistWidget) -> None:
        item = playlist.currentItem()
        if item is None:
            QMessageBox.information(self, "Analyze", "Select a track to analyze.")
            return
        file_path = playlist.file_path_at(playlist.currentRow())
        if not file_path:
            QMessageBox.warning(self, "Analyze", "Selected track has no file path.")
            return
        if self.current_playing_item is item:
            self._set_bpm_label_detecting()
        self.file_analyzer.analyze_item(item, file_path, force=True)

    def analyze_playlist_bpm(self, playlist: PlaylistWidget) -> None:
        """Backward-compatible alias."""
        self.analyze_playlist_files(playlist)

    def reanalyze_track_bpm(self, playlist: PlaylistWidget) -> None:
        """Backward-compatible alias."""
        self.analyze_selected_file(playlist)

    def _on_item_analyzed(self, item, file_path: str, bpm: object, peaks: object) -> None:
        widget = item.listWidget() if item is not None else None
        if isinstance(bpm, (int, float)):
            set_item_bpm(item, float(bpm))
            if widget is not None:
                widget.viewport().update()
            self._save_playlist_for_item(item)
            if self.current_playing_item is item:
                self._set_bpm_label_value(float(bpm))
        if isinstance(peaks, list) and peaks:
            self._store_waveform_cache(file_path, peaks, 0, 0)
            self._apply_analysis_peaks_if_current(file_path, peaks)

    def _on_file_analyzed(self, file_path: str, bpm: object, peaks: object) -> None:
        if isinstance(bpm, (int, float)):
            current_abs = os.path.abspath(self._current_file_path) if self._current_file_path else None
            if current_abs == os.path.abspath(file_path):
                self._set_bpm_label_value(float(bpm))
                if self.current_playing_item:
                    set_item_bpm(self.current_playing_item, float(bpm))
                    self._save_playlist_for_item(self.current_playing_item)
        if isinstance(peaks, list) and peaks:
            self._store_waveform_cache(file_path, peaks, 0, 0)
            self._apply_analysis_peaks_if_current(file_path, peaks)

    def _apply_analysis_peaks_if_current(self, file_path: str, peaks: list) -> None:
        abs_path = os.path.abspath(file_path)
        current = self._current_file_path or self._displayed_track_path
        if current and os.path.abspath(current) == abs_path:
            width = self.waveform.width()
            if width > 0:
                from app.analysis_cache import resample_peaks

                num_bars = self._waveform_num_bars(width)
                data = resample_peaks(peaks, num_bars) if len(peaks) != num_bars else peaks
                self._store_waveform_cache(file_path, data, 0, 0)
                self.waveform.set_waveform(data)
        preview_path = getattr(self, "_preview_file_path", None)
        if preview_path and os.path.abspath(preview_path) == abs_path:
            width = self.preview_waveform.width()
            if width > 0:
                from app.analysis_cache import resample_peaks

                num_bars = self._waveform_num_bars(width)
                data = resample_peaks(peaks, num_bars) if len(peaks) != num_bars else peaks
                self.preview_waveform.set_waveform(data)

    def _on_playlist_item_probed(self, item) -> None:
        widget = item.listWidget()
        if widget is not None:
            self.refresh_playlist_footer(widget)

    def on_time_mode_changed(self, mode: str) -> None:
        self._time_display_mode = mode
        self.update_time_display()

    def on_playback_mode_changed(self, mode: str) -> None:
        self.playback_mode = mode

    def adjust_playlist_columns(self, delta: int) -> None:
        value = self.columns_spin.value() + delta
        value = max(self.columns_spin.minimum(), min(self.columns_spin.maximum(), value))
        self.columns_spin.setValue(value)

    def _on_playlist_item_dropped(
        self,
        from_playlist_num: int,
        _from_row: int,
        to_playlist_num: int,
        _to_row: int,
    ) -> None:
        self.refresh_playlist_footer(self.get_playlist(from_playlist_num))
        if to_playlist_num != from_playlist_num:
            self.refresh_playlist_footer(self.get_playlist(to_playlist_num))

    def refresh_playlist_footer(self, playlist: PlaylistWidget | None = None) -> None:
        for i, pl in enumerate(self.playlists):
            if playlist is not None and pl is not playlist:
                continue
            if i >= len(self.playlist_footer_labels):
                continue
            count, total_ms = pl.get_stats()
            hours = total_ms // 3_600_000
            minutes = (total_ms % 3_600_000) // 60_000
            seconds = (total_ms % 60_000) // 1000
            if hours:
                total_text = f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                total_text = f"{minutes}:{seconds:02d}"
            self.playlist_footer_labels[i].setText(
                f"Total Tracks: {count}   Total Time: {total_text}"
            )

    def get_visible_playlists(self) -> list[PlaylistWidget]:
        return self.playlists[:self.columns_spin.value()]

    def get_playlist(self, playlist_num: int) -> PlaylistWidget:
        return self.playlists[playlist_num - 1]

    def default_playlist_title(self, playlist_num: int) -> str:
        return f"Playlist {playlist_num}"

    def get_playlist_title(self, playlist_num: int) -> str:
        custom = self._playlist_titles.get(playlist_num, "").strip()
        return custom or self.default_playlist_title(playlist_num)

    def on_playlist_title_changed(self, playlist_num: int, title: str) -> None:
        title = title.strip()
        default = self.default_playlist_title(playlist_num)
        if not title or title == default:
            self._playlist_titles.pop(playlist_num, None)
            canonical = default
        else:
            self._playlist_titles[playlist_num] = title
            canonical = title

        header = self.playlist_headers[playlist_num - 1]
        if header.title() != canonical:
            header.setTitle(canonical)

    def _apply_playlist_header_titles(self) -> None:
        for i, header in enumerate(self.playlist_headers, start=1):
            header.setTitle(self.get_playlist_title(i))

    def set_playlist_columns(self, count: int):
        for i, panel in enumerate(self.playlist_panels):
            panel.setVisible(i < count)
        if not hasattr(self, "duration_prober"):
            return
        for playlist in self.playlists[:count]:
            self.duration_prober.probe_playlist(playlist)
            playlist.viewport().update()

    def on_playlist_item_clicked(self, playlist_num, item):
        if item is None:
            return
        self.last_selected_playlist = playlist_num
        for i, playlist in enumerate(self.get_visible_playlists(), start=1):
            if i != playlist_num and playlist.currentItem() is not None:
                playlist.blockSignals(True)
                playlist.clearSelection()
                playlist.blockSignals(False)
        # Lock focus before preview load — media/waveform callbacks often steal it.
        self._retain_playlist_keyboard_focus(playlist_num)
        self.load_preview_track(playlist_num, item)
        self._retain_playlist_keyboard_focus(playlist_num)

    def _retain_playlist_keyboard_focus(self, playlist_num: int) -> None:
        """Keep ↑/↓ on the playlist even if preview loading moves focus."""
        self._retain_playlist_focus_num = playlist_num
        for i, playlist in enumerate(self.playlists, start=1):
            playlist.set_reclaim_focus(i == playlist_num)
        self._restore_playlist_keyboard_focus()
        self._update_playlist_focus_highlight()
        QTimer.singleShot(0, self._restore_playlist_keyboard_focus)
        QTimer.singleShot(50, self._restore_playlist_keyboard_focus)
        QTimer.singleShot(200, self._restore_playlist_keyboard_focus)

    def _clear_playlist_focus_retain(self) -> None:
        self._retain_playlist_focus_num = None
        for playlist in self.playlists:
            playlist.set_reclaim_focus(False)
        self._update_playlist_focus_highlight()

    def _restore_playlist_keyboard_focus(self) -> None:
        playlist_num = self._retain_playlist_focus_num
        if playlist_num is None:
            return
        playlist = self.get_playlist(playlist_num)
        if playlist is None or not playlist.isVisible():
            return
        focused = QApplication.focusWidget()
        if focused is not None and focused == playlist:
            return
        if focused is not None:
            from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QMenu

            if isinstance(focused, (QLineEdit, QComboBox, QMenu, QAbstractSpinBox)):
                return
            if self._widget_belongs_to(focused, playlist):
                return
        playlist.setFocus(Qt.FocusReason.OtherFocusReason)

    def on_playlist_focused(self, playlist_num: int) -> None:
        self.last_selected_playlist = playlist_num
        self._retain_playlist_keyboard_focus(playlist_num)

    @staticmethod
    def _widget_belongs_to(widget: QWidget | None, ancestor: QWidget) -> bool:
        # App eventFilter also sees QWindow / QObject — not every target is a QWidget.
        # Use == (C++ identity), not `is` — PyQt may wrap the same object twice.
        if not isinstance(widget, QWidget) or not isinstance(ancestor, QWidget):
            return False
        current: QWidget | None = widget
        while current is not None:
            if current == ancestor:
                return True
            current = current.parentWidget()
        return False

    def _is_playlist_nav_key(self, key: int) -> bool:
        return key in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        )

    def _focus_allows_playlist_steal(self, widget: QWidget | None) -> bool:
        """False when the user is intentionally typing / picking in a control."""
        if widget is None:
            return True
        from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QMenu

        if isinstance(widget, (QLineEdit, QComboBox, QMenu, QAbstractSpinBox)):
            return False
        if hasattr(self, "file_browser") and self._widget_belongs_to(widget, self.file_browser):
            return False
        return True

    def _on_app_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        if self._retain_playlist_focus_num is not None and new is not None:
            from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QMenu

            playlist = self.get_playlist(self._retain_playlist_focus_num)
            if isinstance(new, (QLineEdit, QComboBox, QMenu, QAbstractSpinBox)):
                self._clear_playlist_focus_retain()
            elif hasattr(self, "file_browser") and self._widget_belongs_to(new, self.file_browser):
                self._clear_playlist_focus_retain()
            elif playlist is not None and new != playlist:
                for i, other in enumerate(self.playlists, start=1):
                    if other == new and i != self._retain_playlist_focus_num:
                        self._retain_playlist_keyboard_focus(i)
                        break

        self._update_playlist_focus_highlight()
        if hasattr(self, "file_browser") and self.file_browser is not None:
            self.file_browser._update_focus_highlight()

        if self._retain_playlist_focus_num is None:
            return
        playlist = self.get_playlist(self._retain_playlist_focus_num)
        if playlist is None:
            return
        if new == playlist or self._widget_belongs_to(new, playlist):
            return
        if not self._focus_allows_playlist_steal(new):
            return
        QTimer.singleShot(0, self._restore_playlist_keyboard_focus)

    def _update_playlist_focus_highlight(self) -> None:
        focused = QApplication.focusWidget()
        for index, playlist in enumerate(self.playlists):
            panel = (
                self.playlist_panels[index]
                if index < len(self.playlist_panels)
                else None
            )
            num = index + 1
            in_panel = panel is not None and self._widget_belongs_to(focused, panel)
            retained = self._retain_playlist_focus_num == num
            # Keep blue ring while this playlist is the keyboard target,
            # even if Qt focus briefly jumps during preview load / style polish.
            is_active = retained or in_panel or (
                focused is None and self.last_selected_playlist == num
            )
            list_frame = playlist.parentWidget()
            if list_frame is None:
                continue
            previous = list_frame.property("playlistActive")
            if bool(previous) == bool(is_active):
                continue
            list_frame.setProperty("playlistActive", is_active)
            style = list_frame.style()
            style.unpolish(list_frame)
            style.polish(list_frame)
            list_frame.update()

    def get_selected_track(self) -> tuple[int | None, object | None]:
        if self.last_selected_playlist is not None:
            playlist = self.get_playlist(self.last_selected_playlist)
            if playlist.isVisible():
                item = playlist.currentItem()
                if item:
                    return self.last_selected_playlist, item

        for i, playlist in enumerate(self.get_visible_playlists(), start=1):
            item = playlist.currentItem()
            if item:
                return i, item
        return None, None

    @staticmethod
    def get_track_display_name(item, file_path: str | None) -> str:
        if item and item.text():
            return item.text()
        if file_path:
            return os.path.basename(file_path)
        return "Unknown"

    def on_playlist_item_renamed(self, playlist_num, item):
        playlist = self.get_playlist(playlist_num)
        playlist.viewport().update()
        if self.current_playing_item is not item:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.update_track_info_playing()
        else:
            self.update_track_info_ready()

    def set_playback_mode(self, mode):
        self.playback_mode = mode
        if hasattr(self, "playback_mode_group"):
            self.playback_mode_group.set_value(mode)

    def change_playlist_font(self, playlist_num, size):
        family = get_token("typography.font_family_ui", "Arial")
        self.get_playlist(playlist_num).set_playlist_font(QFont(family, size))

    def refresh_playlist_display(self):
        for index, playlist in enumerate(self.playlists):
            if index < len(self.font_size_spins):
                self.change_playlist_font(index + 1, self.font_size_spins[index].value())
            playlist.viewport().update()
        for playlist in self.get_visible_playlists():
            self.duration_prober.probe_playlist(playlist)
        for playlist in self.playlists:
            self.refresh_playlist_footer(playlist)

    def setup_connections(self):
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_app_focus_changed)
        for idx, media_player in enumerate(self._players):
            media_player.positionChanged.connect(
                lambda position, player_idx=idx: self._on_player_position_changed(player_idx, position)
            )
            media_player.durationChanged.connect(
                lambda duration, player_idx=idx: self._on_player_duration_changed(player_idx, duration)
            )
            media_player.mediaStatusChanged.connect(
                lambda status, player_idx=idx: self._on_player_media_status_changed(player_idx, status)
            )
            media_player.playbackStateChanged.connect(
                lambda state, player_idx=idx: self._on_player_playback_state_changed(player_idx, state)
            )
        self.waveform.positionChanged.connect(self.seek)
        self.waveform.rangeChanged.connect(self._on_waveform_range_changed)
        self.waveform.fadeChanged.connect(self._on_waveform_fade_changed)
        self.waveform.widthChanged.connect(self.regenerate_waveform)
        self.waveform.viewChanged.connect(self._on_waveform_view_changed)
        self.preview_waveform.positionChanged.connect(self.seek_preview)
        self.preview_waveform.rangeChanged.connect(self._on_preview_waveform_range_changed)
        self.preview_waveform.fadeChanged.connect(self._on_preview_waveform_fade_changed)
        self.preview_waveform.widthChanged.connect(self.regenerate_preview_waveform)
        self.preview_waveform.viewChanged.connect(self._on_preview_waveform_view_changed)
        self.browser_waveform.positionChanged.connect(self.seek_browser)
        self.browser_waveform.widthChanged.connect(self.regenerate_browser_waveform)
        self.btn_browser_play.clicked.connect(self.browser_play)
        self.btn_browser_pause.clicked.connect(self.browser_pause)
        self.btn_browser_stop.clicked.connect(self.browser_stop)
        self.btn_browser_autoplay.toggled.connect(self.on_browser_autoplay_toggled)
        self.btn_browser_rewind_start.clicked.connect(self.rewind_browser_to_start)
        self.file_browser.audioSelected.connect(self.load_browser_preview)
        self.file_browser.ejectRequested.connect(self.clear_browser_preview)
        if hasattr(self, "btn_preview_eject"):
            self.btn_preview_eject.clicked.connect(self.clear_preview_deck)
        if hasattr(self, "btn_air_eject"):
            self.btn_air_eject.clicked.connect(self.clear_air_deck)

    def _cancel_crossfade(self) -> None:
        self._crossfade_timer.stop()
        self._outgoing_fader.cancel()
        self._incoming_fader.cancel()
        for idx in {self._crossfade_outgoing_idx, self._crossfade_incoming_idx}:
            if idx is not None:
                self._players[idx].stop()
                if idx == self._active_player_idx:
                    self._apply_player_volume(idx, self._playback_volume)
                else:
                    self._apply_player_volume(idx, 0.0)
        self._cancel_pending_playback()
        self._playback_token += 1
        self._crossfade_incoming_path = None
        self._crossfade_outgoing_idx = None
        self._crossfade_incoming_idx = None
        self._crossfade_active = False
        self._crossfade_fade_in_ms = 0
        self._crossfade_incoming_ready_to_play = False
        self._suppress_range_end = False
        self._pending_transition_fade_ms = 0

    def _start_crossfade_transition(
        self,
        file_path: str,
        abs_path: str,
        fade_ms: int,
        *,
        start_ms: int | None = None,
    ) -> None:
        incoming_idx = 1 - self._active_player_idx
        self._crossfade_active = True
        self._crossfade_fade_in_ms = max(1, fade_ms)
        self._crossfade_incoming_path = file_path
        self._crossfade_outgoing_idx = self._active_player_idx
        self._crossfade_incoming_idx = incoming_idx
        self._crossfade_incoming_ready_to_play = False

        self._playback_token += 1
        self._pending_playback_token = self._playback_token
        self._pending_playback_start = True
        self._pending_playback_player_idx = incoming_idx
        self._pending_start_ms = start_ms

        self._prepare_incoming_track(file_path, abs_path)
        start_pos = start_ms if start_ms is not None else self._range_start_ms()
        self.waveform.set_position(start_pos)
        self._apply_player_volume(incoming_idx, 0.0)
        self._incoming_fader.sync_volume(0.0)
        if not self._air_player_has_loaded_source(incoming_idx, abs_path):
            self._set_air_source(incoming_idx, file_path)
        else:
            # Reused deck: still refresh EQ for the incoming path.
            self._players[incoming_idx].set_eq_state(self._eq_state_for_path(file_path))

        self._outgoing_fader.sync_volume(self._playback_volume)
        self._outgoing_fader.fade_to(
            0.0,
            fade_ms,
            on_complete=self._stop_outgoing_player,
        )
        # Incoming starts immediately (same moment as outgoing fade-out).
        self._begin_incoming_crossfade()

    def _stop_outgoing_player(self) -> None:
        idx = self._crossfade_outgoing_idx
        if idx is None:
            return
        self._players[idx].stop()
        self._apply_player_volume(idx, self._playback_volume)

    def _finish_crossfade(self) -> None:
        self._stop_outgoing_player()
        self._crossfade_active = False
        self._crossfade_fade_in_ms = 0
        self._crossfade_incoming_path = None
        self._crossfade_outgoing_idx = None
        self._crossfade_incoming_idx = None
        self._crossfade_incoming_ready_to_play = False
        self._pending_playback_player_idx = None
        self._set_output_volume(self._playback_volume)

    def _prepare_incoming_crossfade_media(self, duration: int) -> None:
        if not self._current_file_path:
            return
        self.waveform.set_duration(duration)
        self._restore_timeline_state(self._current_file_path, duration)
        if self._pending_start_ms is None:
            self._pending_start_ms = self._range_start_ms()
            self.waveform.set_position(self._pending_start_ms)

    def _on_crossfade_switch_timeout(self) -> None:
        self._begin_incoming_crossfade()

    def _begin_incoming_crossfade(self) -> None:
        if not self._crossfade_active or not self._crossfade_incoming_path:
            return
        self._crossfade_incoming_ready_to_play = True
        if self._pending_start_ms is None:
            self._pending_start_ms = self._range_start_ms()
            self.waveform.set_position(self._pending_start_ms)
        self._try_begin_pending_playback()

    def _prepare_incoming_track(self, file_path: str, abs_path: str) -> None:
        if self._displayed_track_path and self._displayed_track_path != abs_path:
            self._save_timeline_for_path(self._displayed_track_path)
        if self._current_file_path and os.path.abspath(self._current_file_path) != abs_path:
            self._save_timeline_for_path(self._current_file_path)

        self._current_file_path = file_path
        self._fade_out_started = False
        is_new_track = self._displayed_track_path != abs_path
        needs_waveform = is_new_track or not self.waveform.waveform_data
        self.waveform.reset_view()
        if is_new_track:
            self.waveform.set_waveform([])
        elif needs_waveform:
            self.waveform.set_waveform([])
        self._displayed_track_path = abs_path
        self._prepare_timeline_for_path(file_path)
        self.update_track_info_playing()
        self._update_file_properties(file_path)
        if needs_waveform:
            QTimer.singleShot(0, lambda path=file_path: self._request_waveform(path))
        self._request_bpm_detection(file_path)
        self.update_time_display()
        self._update_timeline_labels()

    def _is_incoming_crossfade_loading(self) -> bool:
        return (
            self._crossfade_active
            and self._crossfade_incoming_idx is not None
            and self._pending_playback_start
            and self._pending_playback_player_idx == self._crossfade_incoming_idx
        )

    def toggle_fade_mode(self) -> None:
        if self._fade_mode == "crossfade":
            self._fade_mode = "sequential"
        else:
            self._fade_mode = "crossfade"
        self._update_fade_mode_button()

    def _update_fade_mode_button(self) -> None:
        if not hasattr(self, "btn_fade_mode"):
            return
        icon_name = "fade_mode_2" if self._fade_mode == "crossfade" else "fade_mode_1"
        icon_px = get_token_int("sizes.icon_button", 18)
        self.btn_fade_mode.setIcon(load_icon(self, icon_name, icon_px))
        self.btn_fade_mode.setIconSize(icon_size(icon_px))
        if self._fade_mode == "crossfade":
            self.btn_fade_mode.setToolTip(
                "Crossfade: outgoing fade out and incoming fade in overlap"
            )
        else:
            self.btn_fade_mode.setToolTip(
                "Sequential: fade out completes before the next track fades in"
            )

    def _is_crossfade_mode(self) -> bool:
        return self._fade_mode == "crossfade"

    def _timeline_fades_enabled(self) -> bool:
        return not self._is_crossfade_mode()

    def reset_waveform_zoom(self) -> None:
        self.waveform.reset_view()

    def rewind_to_start(self) -> None:
        if self.player.duration() <= 0 and not self._current_file_path:
            return
        start_ms = self._range_start_ms()
        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self._range_end_latched = False
        self._fade_out_started = False
        if self.player.duration() > 0:
            self.player.setPosition(start_ms)
            self._seek_target_ms = start_ms if start_ms > 0 else None
        self.waveform.set_position(start_ms)
        self.volume_fader.cancel()
        if was_playing:
            self._apply_timeline_volume(start_ms)
        elif self._timeline_fades_enabled():
            self._set_output_volume(self._volume_for_position(start_ms))
        else:
            self._set_output_volume(self._playback_volume)
        self.update_time_display()

    def on_waveform_res_toggled(self, checked: bool) -> None:
        self._waveform_res_enabled = checked
        if self._current_file_path and (self.waveform.is_zoomed or not checked):
            self._request_waveform(self._current_file_path, self.waveform.width())

    def _refresh_res_waveform(self) -> None:
        if self._current_file_path and self.waveform.is_zoomed and self._waveform_res_enabled:
            self._request_waveform(self._current_file_path, self.waveform.width())

    def _on_waveform_view_changed(self) -> None:
        zoomed = self.waveform.is_zoomed
        zoom_changed = zoomed != self._waveform_was_zoomed
        self._waveform_was_zoomed = zoomed

        if hasattr(self, "btn_zoom_reset"):
            self.btn_zoom_reset.setEnabled(zoomed)
        if hasattr(self, "btn_waveform_res"):
            self.btn_waveform_res.setEnabled(zoomed)
            if not zoomed and self.btn_waveform_res.isChecked():
                self.btn_waveform_res.blockSignals(True)
                self.btn_waveform_res.setChecked(False)
                self.btn_waveform_res.blockSignals(False)
                self._waveform_res_enabled = False

        if not self._current_file_path:
            return
        if zoomed and self._waveform_res_enabled:
            self._res_waveform_timer.start(120)
        elif zoom_changed and not zoomed:
            self._request_waveform(self._current_file_path, self.waveform.width())

    def _update_zoom_reset_button(self) -> None:
        self._on_waveform_view_changed()

    def regenerate_waveform(self, width: int):
        if self._is_window_resizing or not self._current_file_path or width <= 0:
            return
        self._request_waveform(self._current_file_path, width)

    def _waveform_num_bars(self, width: int) -> int:
        return max(1000, width * 4)

    def _waveform_load_params(
        self,
        waveform: AudioWaveform,
        file_path: str,
        width: int,
        res_enabled: bool,
    ) -> tuple[int, int, int]:
        num_bars = self._waveform_num_bars(width)
        start_ms = 0
        end_ms = 0
        if res_enabled and waveform.is_zoomed:
            start_ms = waveform.view_start_ms
            end_ms = waveform.visible_end_ms
        return num_bars, start_ms, end_ms

    def _apply_waveform_entry(
        self,
        waveform: AudioWaveform,
        entry: WaveformCacheEntry,
        start_ms: int,
        end_ms: int,
    ) -> None:
        if start_ms > 0 or end_ms > start_ms:
            map_start = entry.map_start_ms if entry.map_start_ms > 0 else start_ms
            map_end = entry.map_end_ms if entry.map_end_ms > 0 else end_ms
            waveform.set_waveform(entry.data, map_start_ms=map_start, map_end_ms=map_end)
        else:
            waveform.set_waveform(entry.data)

    def _try_apply_cached_waveform(
        self,
        waveform: AudioWaveform,
        file_path: str,
        width: int,
        res_enabled: bool,
        *,
        store: bool = True,
    ) -> bool:
        if width <= 0 or not file_path:
            return False
        num_bars, start_ms, end_ms = self._waveform_load_params(
            waveform,
            file_path,
            width,
            res_enabled,
        )
        key = WaveformCacheKey.create(
            file_path,
            num_bars,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        entry = self._waveform_cache.get(key)
        if entry is not None and entry.data:
            self._apply_waveform_entry(waveform, entry, start_ms, end_ms)
            return True

        # Disk cache: full-track only (not zoomed RES windows)
        if start_ms == 0 and end_ms == 0:
            peaks = load_peaks(file_path, num_bars)
            if peaks:
                if store:
                    self._store_waveform_cache(file_path, peaks, 0, 0)
                self._apply_waveform_entry(
                    waveform,
                    WaveformCacheEntry(data=peaks),
                    0,
                    0,
                )
                return True
        return False

    def _store_waveform_cache(
        self,
        file_path: str,
        data: list,
        start_ms: int,
        end_ms: int,
    ) -> None:
        if not data:
            return
        key = WaveformCacheKey.create(
            file_path,
            len(data),
            start_ms=start_ms,
            end_ms=end_ms,
        )
        map_start = start_ms if start_ms > 0 else 0
        map_end = end_ms if end_ms > start_ms else 0
        self._waveform_cache.put(
            key,
            WaveformCacheEntry(
                data=list(data),
                map_start_ms=map_start,
                map_end_ms=map_end,
            ),
        )

    def _request_waveform(self, file_path: str, width: int | None = None) -> None:
        if not file_path:
            return
        if width is None:
            width = self.waveform.width()
        if width <= 0:
            return
        if self._try_apply_cached_waveform(
            self.waveform,
            file_path,
            width,
            self._waveform_res_enabled,
        ):
            return
        num_bars, start_ms, end_ms = self._waveform_load_params(
            self.waveform,
            file_path,
            width,
            self._waveform_res_enabled,
        )
        self._waveform_request_id = self.waveform_loader.request(
            file_path,
            num_bars,
            start_ms=start_ms,
            end_ms=end_ms,
        )

    def _on_waveform_ready(
        self,
        file_path: str,
        data: list,
        request_id: int,
        start_ms: int,
        end_ms: int,
    ) -> None:
        if request_id != self._waveform_request_id:
            return
        current_path = self._current_file_path or self._displayed_track_path
        if not current_path:
            return
        if os.path.abspath(file_path) != os.path.abspath(current_path):
            return
        self._store_waveform_cache(file_path, data, start_ms, end_ms)
        # Persist full-track peaks so waveform survives restart
        if (start_ms == 0 and end_ms == 0) and data:
            from app.analysis_cache import (
                CANONICAL_WAVEFORM_BARS,
                has_waveform_cache,
                load_bpm,
                resample_peaks,
                save_analysis,
            )

            if not has_waveform_cache(file_path):
                peaks = (
                    data
                    if len(data) >= CANONICAL_WAVEFORM_BARS
                    else resample_peaks(data, CANONICAL_WAVEFORM_BARS)
                )
                bpm = load_bpm(file_path)
                if self.current_playing_item:
                    stored = get_item_bpm(self.current_playing_item)
                    if stored is not None:
                        bpm = stored
                save_analysis(file_path, bpm=bpm, peaks=peaks, num_bars=len(peaks))
        if start_ms > 0 or end_ms > start_ms:
            self.waveform.set_waveform(data, map_start_ms=start_ms, map_end_ms=end_ms)
        else:
            self.waveform.set_waveform(data)

    def resizeEvent(self, event):
        new_size = event.size()
        if self._stable_window_size is None:
            self._stable_window_size = new_size
            super().resizeEvent(event)
            return

        if new_size == self._stable_window_size:
            super().resizeEvent(event)
            return

        if not self._is_window_resizing:
            self._is_window_resizing = True
            self.waveform.set_deferred_render(True)
            self.preview_waveform.set_deferred_render(True)
        self._resize_finish_timer.start(200)
        super().resizeEvent(event)

    def _finish_window_resize(self):
        if not self._is_window_resizing:
            return
        self._is_window_resizing = False
        self._stable_window_size = self.size()
        self.waveform.set_deferred_render(False)
        self.preview_waveform.set_deferred_render(False)
        self.regenerate_waveform(self.waveform.width())
        self.regenerate_preview_waveform(self.preview_waveform.width())
        self.waveform.set_position(self.player.position())
        self.preview_waveform.set_position(self._preview_player.position())

    @staticmethod
    def _timeline_state_to_dict(state: TimelineState) -> dict:
        return {
            "range_start_ms": state.range_start_ms,
            "range_end_ms": state.range_end_ms,
            "fade_in_ms": state.fade_in_ms,
            "fade_out_ms": state.fade_out_ms,
        }

    @staticmethod
    def _timeline_state_from_dict(data: dict) -> TimelineState:
        return TimelineState(
            range_start_ms=int(data.get("range_start_ms", 0)),
            range_end_ms=int(data.get("range_end_ms", 0)),
            fade_in_ms=int(data.get("fade_in_ms", 0)),
            fade_out_ms=int(data.get("fade_out_ms", 0)),
        )

    def _window_title(self) -> str:
        name = getattr(self.project, "name", None) or "Untitled"
        if getattr(self.project, "is_untitled", lambda: False)():
            return f"{APP_NAME} — Untitled"
        return f"{APP_NAME} — {name}"

    def _update_window_title(self) -> None:
        self.setWindowTitle(self._window_title())

    def _setup_menus(self) -> None:
        self._setup_project_menu()
        self._setup_view_menu()
        self._setup_about_menu()

    def _setup_project_menu(self) -> None:
        menu = self.menuBar().addMenu("Project")
        act_new = QAction("New Project", self)
        act_new.triggered.connect(self.new_project)
        menu.addAction(act_new)
        act_open = QAction("Open Project…", self)
        act_open.triggered.connect(self.open_project)
        menu.addAction(act_open)
        menu.addSeparator()
        act_save = QAction("Save Project", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self.save_project)
        menu.addAction(act_save)
        act_save_as = QAction("Save Project As…", self)
        act_save_as.triggered.connect(self.save_project_as)
        menu.addAction(act_save_as)

    def _setup_view_menu(self) -> None:
        menu = self.menuBar().addMenu("View")
        menu.aboutToShow.connect(self._sync_view_menu_actions)

        self._view_file_browser_act = QAction("File Browser", self)
        self._view_file_browser_act.setCheckable(True)
        self._view_file_browser_act.toggled.connect(self._on_view_file_browser_toggled)
        menu.addAction(self._view_file_browser_act)

        self._view_control_panel_act = QAction("Control Panel", self)
        self._view_control_panel_act.setCheckable(True)
        self._view_control_panel_act.toggled.connect(self._on_view_control_panel_toggled)
        menu.addAction(self._view_control_panel_act)

        self._view_file_properties_act = QAction("Preview Properties", self)
        self._view_file_properties_act.setCheckable(True)
        self._view_file_properties_act.toggled.connect(self._on_view_file_properties_toggled)
        menu.addAction(self._view_file_properties_act)

        self._view_timeline_act = QAction("Timeline", self)
        self._view_timeline_act.setCheckable(True)
        self._view_timeline_act.toggled.connect(self._on_view_timeline_toggled)
        menu.addAction(self._view_timeline_act)

        self._view_playlists_act = QAction("Playlists", self)
        self._view_playlists_act.setCheckable(True)
        self._view_playlists_act.toggled.connect(self._on_view_playlists_toggled)
        menu.addAction(self._view_playlists_act)

        self._sync_view_menu_actions()

    def _setup_about_menu(self) -> None:
        menu = self.menuBar().addMenu("About")
        act_about = QAction(f"About {APP_NAME}…", self)
        act_about.triggered.connect(self._show_about_dialog)
        menu.addAction(act_about)

    def _sync_view_menu_actions(self) -> None:
        actions = (
            getattr(self, "_view_file_browser_act", None),
            getattr(self, "_view_control_panel_act", None),
            getattr(self, "_view_file_properties_act", None),
            getattr(self, "_view_timeline_act", None),
            getattr(self, "_view_playlists_act", None),
        )
        if not all(actions):
            return

        def set_checked(action: QAction, checked: bool) -> None:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)

        set_checked(self._view_file_browser_act, not self._browser_collapsed)
        if hasattr(self, "control_panel_section"):
            set_checked(
                self._view_control_panel_act,
                not self.control_panel_section.isHidden(),
            )
        set_checked(self._view_file_properties_act, not self._properties_collapsed)
        if hasattr(self, "timeline_section"):
            set_checked(
                self._view_timeline_act,
                not self.timeline_section.isHidden(),
            )
        if hasattr(self, "playlist_section"):
            set_checked(
                self._view_playlists_act,
                not self.playlist_section.isHidden(),
            )

    def _on_view_file_browser_toggled(self, checked: bool) -> None:
        if checked:
            self.expand_file_browser()
        else:
            self.collapse_file_browser()

    def _on_view_control_panel_toggled(self, checked: bool) -> None:
        if hasattr(self, "control_panel_section"):
            self.control_panel_section.setVisible(checked)
            self.save_config()

    def _on_view_file_properties_toggled(self, checked: bool) -> None:
        if checked:
            self.expand_file_properties()
        else:
            self.collapse_file_properties()

    def _on_view_timeline_toggled(self, checked: bool) -> None:
        if hasattr(self, "timeline_section"):
            self.timeline_section.setVisible(checked)
            self.save_config()

    def _on_view_playlists_toggled(self, checked: bool) -> None:
        if hasattr(self, "playlist_section"):
            self.playlist_section.setVisible(checked)
            self.save_config()

    def _show_about_dialog(self) -> None:
        show_about_dialog(self)

    def _show_playlist_context_menu(self, playlist: PlaylistWidget, pos: QPoint) -> None:
        item = playlist.itemAt(pos)
        if item is None:
            return
        playlist.setCurrentItem(item)
        menu = QMenu(self)
        move_act = menu.addAction("Move to project folder")
        path = get_item_file_path(item)
        if path and self.project.is_path_in_project(path):
            move_act.setEnabled(False)
            move_act.setText("Already in project")

        color_menu = menu.addMenu("Mark color")
        current_color = get_item_color(item)
        clear_act = color_menu.addAction("None")
        clear_act.setCheckable(True)
        clear_act.setChecked(current_color is None)
        color_actions: list[tuple[object, str | None]] = [(clear_act, None)]
        for label, hex_color in TRACK_COLOR_PRESETS:
            act = color_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(current_color == hex_color)
            color_actions.append((act, hex_color))

        chosen = menu.exec(playlist.mapToGlobal(pos))
        if chosen is move_act and move_act.isEnabled():
            self.move_selected_track_to_project(playlist)
            return
        for act, hex_color in color_actions:
            if chosen is act:
                set_item_color(item, hex_color)
                playlist.viewport().update()
                self.save_project()
                return

    def _retarget_path_references(self, old_path: str, new_path: str) -> None:
        old_abs = os.path.abspath(old_path)
        new_abs = os.path.abspath(new_path)
        if old_abs == new_abs:
            return

        if old_abs in self._track_timeline_state:
            self._track_timeline_state[new_abs] = self._track_timeline_state.pop(old_abs)
        if old_abs in self._track_eq_state:
                self._track_eq_state[new_abs] = self._track_eq_state.pop(old_abs)
        if self._eq_bound_path == old_abs:
            self._eq_bound_path = new_abs

        if self._current_file_path and os.path.abspath(self._current_file_path) == old_abs:
            self._current_file_path = new_abs
        if self._displayed_track_path and os.path.abspath(self._displayed_track_path) == old_abs:
            self._displayed_track_path = new_abs
        if self._preview_file_path and os.path.abspath(self._preview_file_path) == old_abs:
            self._preview_file_path = new_abs
        if self._preview_displayed_track_path and os.path.abspath(
            self._preview_displayed_track_path
        ) == old_abs:
            self._preview_displayed_track_path = new_abs

    def _clone_path_metadata(self, old_path: str, new_path: str) -> None:
        """Copy timeline/EQ for a duplicated file without touching the live deck."""
        old_abs = os.path.abspath(old_path)
        new_abs = os.path.abspath(new_path)
        if old_abs == new_abs:
            return
        if old_abs in self._track_timeline_state and new_abs not in self._track_timeline_state:
            self._track_timeline_state[new_abs] = self._track_timeline_state[old_abs]
        if old_abs in self._track_eq_state and new_abs not in self._track_eq_state:
            self._track_eq_state[new_abs] = dict(self._track_eq_state[old_abs])

    def move_selected_track_to_project(self, playlist: PlaylistWidget) -> None:
        item = playlist.currentItem()
        if item is None:
            QMessageBox.information(self, "Project", "Select a track first.")
            return
        src = get_item_file_path(item)
        if not src:
            QMessageBox.warning(self, "Project", "Track has no file path.")
            return
        if self.project.is_path_in_project(src):
            QMessageBox.information(self, "Project", "Track is already inside the project.")
            return
        if not os.path.isfile(src):
            QMessageBox.warning(self, "Project", "Source file is missing.")
            return

        new_path = self.project.import_track(src, move=True)
        if not new_path:
            QMessageBox.warning(self, "Project", "Could not move the file into the project.")
            return

        playlist.update_item_path(item, new_path)
        self._retarget_path_references(src, new_path)
        if hasattr(self, "file_browser"):
            self.file_browser.refresh_project_tab()
        self.save_project()
        self.refresh_playlist_footer(playlist)

    def copy_playlist_tracks_to_project(self, playlist: PlaylistWidget) -> None:
        if playlist.count() == 0:
            QMessageBox.information(self, "Project", "Playlist is empty.")
            return

        pending: list[tuple[object, str]] = []
        already = 0
        missing = 0
        for row in range(playlist.count()):
            item = playlist.item(row)
            src = get_item_file_path(item)
            if not src:
                missing += 1
                continue
            if self.project.is_path_in_project(src):
                already += 1
                continue
            if not os.path.isfile(src):
                missing += 1
                continue
            pending.append((item, src))

        if not pending:
            QMessageBox.information(
                self,
                "Project",
                "Nothing to copy — tracks are already in the project, or files are missing.",
            )
            return

        title = self.get_playlist_title(playlist.playlist_num)
        answer = QMessageBox.question(
            self,
            "Copy to Project",
            f"Copy {len(pending)} track(s) from {title} into the project folder?\n"
            "Original files will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        copied_by_src: dict[str, str] = {}
        copied = 0
        failed = 0
        for item, src in pending:
            src_abs = os.path.abspath(src)
            new_path = copied_by_src.get(src_abs)
            if new_path is None:
                new_path = self.project.import_track(src, move=False)
                if not new_path:
                    failed += 1
                    continue
                copied_by_src[src_abs] = new_path
                self._clone_path_metadata(src, new_path)
            playlist.update_item_path(item, new_path)
            copied += 1

        if hasattr(self, "file_browser"):
            self.file_browser.refresh_project_tab()
        if copied:
            self.save_project()
        self.refresh_playlist_footer(playlist)

        details = [f"Copied {copied} track(s) into the project."]
        if already:
            details.append(f"{already} already in the project.")
        if missing:
            details.append(f"{missing} missing on disk.")
        if failed:
            details.append(f"{failed} failed to copy.")
        if failed:
            QMessageBox.warning(self, "Project", "\n".join(details))
        else:
            QMessageBox.information(self, "Project", "\n".join(details))

    def _build_project_state(self) -> ProjectState:
        self._save_current_timeline_state()
        playlists = self.project.collect_playlist_entries(
            self.playlists, self._track_timeline_state
        )
        for i, pl_state in enumerate(playlists):
            num = i + 1
            title = self._playlist_titles.get(num, "").strip()
            if not title and hasattr(self, "playlist_headers") and i < len(self.playlist_headers):
                title = self.playlist_headers[i].title().strip()
            pl_state.title = title if title != self.default_playlist_title(num) else ""
            if i < len(self.font_size_spins):
                pl_state.font_size = self.font_size_spins[i].value()

        return ProjectState(
            name=self.project.name,
            playlists=playlists,
            playlist_columns=self.columns_spin.value() if hasattr(self, "columns_spin") else 2,
            track_timelines={
                path: self._timeline_state_to_dict(state)
                for path, state in self._track_timeline_state.items()
            },
            track_eq=dict(self._track_eq_state),
            video_mixer=self._project_video_mixer,
        )

    def save_project(self) -> None:
        if getattr(self, "_saving_project", False):
            return
        if self.project.is_untitled():
            self.save_project_as()
            return
        self._saving_project = True
        try:
            state = self._build_project_state()
            self.project.save_raw(state)
            paths: list[str] = []
            for playlist in self.playlists:
                paths.extend(playlist.all_file_paths())
            self.project.export_analysis_for_paths(paths)
            # Keep legacy playlist JSON in sync for older tools / backup
            self.save_all_playlists()
            if hasattr(self, "file_browser"):
                self.file_browser.refresh_project_tab()
            self._update_window_title()
        except Exception as exc:
            print(f"Error saving project: {exc}")
            QMessageBox.warning(self, "Project", f"Could not save project:\n{exc}")
        finally:
            self._saving_project = False

    def save_project_as(self) -> None:
        start = str(
            Path.home()
            if self.project.is_untitled()
            else self.project.root.parent
        )
        chosen = QFileDialog.getExistingDirectory(
            self, "Save Project As — choose folder", start
        )
        if not chosen:
            return
        target = Path(chosen)
        try:
            non_empty = any(target.iterdir())
        except OSError:
            non_empty = False
        if non_empty and not (target / "project.json").exists():
            reply = QMessageBox.question(
                self,
                "Save Project As",
                f"Folder “{target.name}” is not empty. Save the project here anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            was_untitled = self.project.is_untitled()
            if was_untitled:
                self.project.relocate_to(target, name=target.name)
            else:
                self.project.open(target)
            if hasattr(self, "file_browser"):
                self.file_browser.set_project_root(
                    str(self.project.root), self.project.name
                )
            self._saving_project = True
            try:
                state = self._build_project_state()
                state.name = self.project.name
                self.project.save_raw(state)
                paths: list[str] = []
                for playlist in self.playlists:
                    paths.extend(playlist.all_file_paths())
                self.project.export_analysis_for_paths(paths)
                self.save_all_playlists()
                if hasattr(self, "file_browser"):
                    self.file_browser.refresh_project_tab()
            finally:
                self._saving_project = False
            self.save_config()
            self._update_window_title()
        except Exception as exc:
            print(f"Error saving project as: {exc}")
            QMessageBox.warning(self, "Project", f"Could not save project:\n{exc}")

    def new_project(self) -> None:
        reply = QMessageBox.question(
            self,
            "New Project",
            "Save the current project before creating a new one?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        if reply == QMessageBox.StandardButton.Yes:
            self.save_project()

        self.project.create_untitled()
        self._project_video_mixer = None
        self._apply_project_state(
            ProjectState(
                name=self.project.name,
                playlists=[ProjectPlaylistState() for _ in range(MAX_PLAYLISTS)],
                video_mixer=None,
            )
        )
        if hasattr(self, "file_browser"):
            self.file_browser.set_project_root(str(self.project.root), self.project.name)
        self.save_config()
        self._update_window_title()

    def open_project(self) -> None:
        reply = QMessageBox.question(
            self,
            "Open Project",
            "Save the current project before opening another?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        if reply == QMessageBox.StandardButton.Yes:
            self.save_project()

        chosen = QFileDialog.getExistingDirectory(
            self, "Open Project — select project folder", str(self.project.root.parent)
        )
        if not chosen:
            return
        target = Path(chosen)
        project_file = target / "project.json"
        if not project_file.is_file():
            QMessageBox.warning(
                self,
                "Open Project",
                "Selected folder has no project.json.",
            )
            return
        self.project.open(target)
        state = self.project.load_raw()
        if state is None:
            QMessageBox.warning(self, "Open Project", "Could not read project.json.")
            return
        self._apply_project_state(state)
        if hasattr(self, "file_browser"):
            self.file_browser.set_project_root(str(self.project.root), self.project.name)
        self.save_config()
        self._update_window_title()
        self.refresh_playlist_display()

    def _apply_project_state(self, state: ProjectState) -> None:
        self.project.name = state.name
        self._track_timeline_state = {
            path: self._timeline_state_from_dict(data)
            for path, data in state.track_timelines.items()
            if isinstance(data, dict)
        }
        self._track_eq_state = {
            path: dict(data)
            for path, data in state.track_eq.items()
            if isinstance(data, dict)
        }
        self._eq_bound_path = None
        self._bind_eq_to_path(self._current_file_path)

        for i, pl_state in enumerate(state.playlists[:MAX_PLAYLISTS]):
            playlist = self.playlists[i]
            load_playlist_from_entries(
                playlist,
                pl_state.tracks,
                clear=True,
                timeline_states=self._track_timeline_state,
            )
            num = i + 1
            if pl_state.title:
                self._playlist_titles[num] = pl_state.title
            else:
                self._playlist_titles.pop(num, None)
            if i < len(self.font_size_spins):
                self.font_size_spins[i].blockSignals(True)
                self.font_size_spins[i].setValue(pl_state.font_size)
                self.font_size_spins[i].blockSignals(False)
                self.change_playlist_font(num, pl_state.font_size)

        if hasattr(self, "playlist_headers"):
            self._apply_playlist_header_titles()

        if hasattr(self, "columns_spin"):
            self.columns_spin.setValue(state.playlist_columns)
            if hasattr(self, "columns_stepper"):
                self.columns_stepper.set_value(state.playlist_columns)

        self._project_video_mixer = state.video_mixer

    def load_project(self) -> None:
        state = self.project.load_raw()
        if state is None:
            # First run / empty project: migrate legacy auto-playlists if present
            self.load_playlists()
            if any(pl.count() for pl in self.playlists):
                self.save_project()
            if hasattr(self, "file_browser"):
                self.file_browser.set_project_root(str(self.project.root), self.project.name)
            return

        self._apply_project_state(state)
        if hasattr(self, "file_browser"):
            self.file_browser.set_project_root(str(self.project.root), self.project.name)

    def load_config(self):
        if not self.config_path.exists():
            return

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Timelines from project take precedence; config is a fallback for legacy
            if not self._track_timeline_state:
                track_timelines = config.get("track_timelines")
                if isinstance(track_timelines, dict):
                    self._track_timeline_state = {
                        path: self._timeline_state_from_dict(data)
                        for path, data in track_timelines.items()
                        if isinstance(data, dict)
                    }

            for i in range(MAX_PLAYLISTS):
                key = f"playlist{i + 1}_font_size"
                if key in config and i < len(self.font_size_spins):
                    # Prefer project font sizes when project already applied them
                    if not self.project.project_file.is_file():
                        size = config[key]
                        self.font_size_spins[i].setValue(size)
                        self.change_playlist_font(i + 1, size)

            for i in range(MAX_PLAYLISTS):
                key = f"playlist{i + 1}_title"
                if key in config and isinstance(config[key], str):
                    if not self.project.project_file.is_file():
                        title = config[key].strip()
                        if title:
                            self._playlist_titles[i + 1] = title
            if hasattr(self, "playlist_headers") and not self.project.project_file.is_file():
                self._apply_playlist_header_titles()

            if "playback_mode" in config:
                mode = config["playback_mode"]
                self.playback_mode = mode
                if hasattr(self, "playback_mode_group"):
                    self.playback_mode_group.set_value(mode)

            if "playlist_columns" in config and not self.project.project_file.is_file():
                self.columns_spin.setValue(config["playlist_columns"])
                if hasattr(self, "columns_stepper"):
                    self.columns_stepper.set_value(config["playlist_columns"])

            if "space_advances_selection" in config:
                self.space_advances_checkbox.setChecked(config["space_advances_selection"])

            if config.get("time_display_mode") == "remaining":
                self._time_display_mode = "remaining"
                if hasattr(self, "time_mode_group"):
                    self.time_mode_group.set_value("remaining")
            else:
                self._time_display_mode = "elapsed"
                if hasattr(self, "time_mode_group"):
                    self.time_mode_group.set_value("elapsed")

            if "fade_duration_ms" in config:
                self.fade_duration_spin.setValue(config["fade_duration_ms"])

            fade_mode = config.get("fade_mode")
            if fade_mode in ("crossfade", "sequential"):
                self._fade_mode = fade_mode
                self._update_fade_mode_button()

            if "waveform_res_enabled" in config:
                self._waveform_res_enabled = bool(config["waveform_res_enabled"])
                if hasattr(self, "btn_waveform_res"):
                    self.btn_waveform_res.setChecked(self._waveform_res_enabled)

            if "preview_autoplay" in config:
                self._preview_autoplay = bool(config["preview_autoplay"])
                if hasattr(self, "btn_preview_autoplay"):
                    self.btn_preview_autoplay.setChecked(self._preview_autoplay)

            if "browser_autoplay" in config:
                self._browser_autoplay = bool(config["browser_autoplay"])
                if hasattr(self, "btn_browser_autoplay"):
                    self.btn_browser_autoplay.setChecked(self._browser_autoplay)

            raw_tabs = config.get("browser_tabs")
            if not isinstance(raw_tabs, list) and self.project.project_file.is_file():
                # One-time migrate tabs previously stored in project.json
                try:
                    with open(self.project.project_file, "r", encoding="utf-8") as pf:
                        proj_data = json.load(pf)
                    if isinstance(proj_data, dict):
                        raw_tabs = proj_data.get("browser_tabs")
                except (OSError, json.JSONDecodeError):
                    raw_tabs = None
            if isinstance(raw_tabs, list) and hasattr(self, "file_browser"):
                tabs: list[tuple[str, str]] = []
                for entry in raw_tabs:
                    if not isinstance(entry, dict):
                        continue
                    tab_path = str(entry.get("path") or "").strip()
                    if not tab_path or not os.path.isdir(tab_path):
                        continue
                    title = str(entry.get("title") or Path(tab_path).name).strip() or "Folder"
                    tabs.append((title, tab_path))
                self.file_browser.restore_user_tabs(tabs)

            dialog_dir = config.get("last_playlist_dialog_dir")
            if isinstance(dialog_dir, str) and os.path.isdir(dialog_dir):
                self._last_playlist_dialog_dir = dialog_dir

            air_device_id = device_id_from_config(config.get("air_output_device_id", ""))
            preview_device_id = device_id_from_config(config.get("preview_output_device_id", ""))
            browser_device_id = device_id_from_config(config.get("browser_output_device_id", ""))
            if air_device_id is not None:
                self._air_output_device_id = air_device_id
            if preview_device_id is not None:
                self._preview_output_device_id = preview_device_id
            if browser_device_id is not None:
                self._browser_output_device_id = browser_device_id
            if hasattr(self, "air_output_combo"):
                populate_audio_output_combo(
                    self.air_output_combo,
                    saved_device_id=self._air_output_device_id,
                )
                populate_audio_output_combo(
                    self.preview_output_combo,
                    saved_device_id=self._preview_output_device_id,
                )
                populate_audio_output_combo(
                    self.browser_output_combo,
                    saved_device_id=self._browser_output_device_id,
                )
                self._apply_air_output_from_id(combo_selected_device_id(self.air_output_combo))
                self._apply_preview_output_from_id(
                    combo_selected_device_id(self.preview_output_combo)
                )
                self._apply_browser_output_from_id(
                    combo_selected_device_id(self.browser_output_combo)
                )

            geometry = config.get("window_geometry")
            if isinstance(geometry, list) and len(geometry) == 4:
                x, y, width, height = geometry
                if width >= self.minimumWidth() and height >= self.minimumHeight():
                    self.setGeometry(int(x), int(y), int(width), int(height))
                    self._stable_window_size = self.size()

            splitter = config.get("audio_browser_splitter")
            if (
                isinstance(splitter, list)
                and len(splitter) == 2
                and hasattr(self, "audio_browser_splitter")
            ):
                sizes = [int(splitter[0]), int(splitter[1])]
                self._browser_saved_splitter = sizes
                self.audio_browser_splitter.setSizes(sizes)

            self._browser_collapsed = bool(config.get("browser_collapsed", False))
            self._apply_browser_collapsed_state(self._browser_collapsed)

            self._properties_collapsed = bool(config.get("properties_collapsed", False))
            self._apply_properties_collapsed_state(self._properties_collapsed)
            if hasattr(self, "file_properties_panel"):
                track_eq = config.get("track_eq")
                if isinstance(track_eq, dict):
                    for key, value in track_eq.items():
                        if isinstance(value, dict) and key:
                            self._track_eq_state[os.path.abspath(str(key))] = dict(value)
                # Legacy global EQ → apply only if no per-track data yet
                legacy_eq = config.get("eq")
                if (
                    isinstance(legacy_eq, dict)
                    and legacy_eq.get("bands")
                    and not self._track_eq_state
                    and self._current_file_path
                ):
                    self._track_eq_state[os.path.abspath(self._current_file_path)] = (
                        dict(legacy_eq)
                    )
                self._eq_bound_path = None
                self._bind_eq_to_path(self._current_file_path)

            if hasattr(self, "control_panel_section"):
                self.control_panel_section.setVisible(
                    bool(config.get("view_control_panel", True))
                )
            if hasattr(self, "timeline_section"):
                self.timeline_section.setVisible(
                    bool(config.get("view_timeline", True))
                )
            if hasattr(self, "playlist_section"):
                self.playlist_section.setVisible(
                    bool(config.get("view_playlists", True))
                )
            self._sync_view_menu_actions()
        except Exception as e:
            print(f"Error loading config: {e}")

    def save_config(self):
        self._save_current_timeline_state()
        self._save_eq_for_bound_path()
        geometry = self.geometry()
        config = {
            "project_path": str(self.project.root),
            "playlist_columns": self.columns_spin.value(),
            "playback_mode": self.playback_mode,
            "space_advances_selection": self.space_advances_checkbox.isChecked(),
            "time_display_mode": self._time_display_mode,
            "fade_duration_ms": self.fade_duration_spin.value(),
            "fade_mode": self._fade_mode,
            "waveform_res_enabled": self._waveform_res_enabled,
            "preview_autoplay": self._preview_autoplay,
            "browser_autoplay": self._browser_autoplay,
            "browser_collapsed": self._browser_collapsed,
            "properties_collapsed": self._properties_collapsed,
            "track_eq": dict(self._track_eq_state),
            "view_control_panel": (
                not self.control_panel_section.isHidden()
                if hasattr(self, "control_panel_section")
                else True
            ),
            "view_timeline": (
                not self.timeline_section.isHidden()
                if hasattr(self, "timeline_section")
                else True
            ),
            "view_playlists": (
                not self.playlist_section.isHidden()
                if hasattr(self, "playlist_section")
                else True
            ),
            "window_geometry": [
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
            ],
            "track_timelines": {
                path: self._timeline_state_to_dict(state)
                for path, state in self._track_timeline_state.items()
            },
            "last_playlist_dialog_dir": self._last_playlist_dialog_dir,
            "air_output_device_id": device_id_to_config(
                combo_selected_device_id(self.air_output_combo) or b""
            ),
            "preview_output_device_id": device_id_to_config(
                combo_selected_device_id(self.preview_output_combo) or b""
            ),
            "browser_output_device_id": device_id_to_config(
                combo_selected_device_id(self.browser_output_combo) or b""
            ),
        }
        if hasattr(self, "audio_browser_splitter"):
            if self._browser_collapsed and self._browser_saved_splitter:
                config["audio_browser_splitter"] = self._browser_saved_splitter
            else:
                config["audio_browser_splitter"] = self.audio_browser_splitter.sizes()
        for i, spin in enumerate(self.font_size_spins, start=1):
            config[f"playlist{i}_font_size"] = spin.value()
        for i in range(1, MAX_PLAYLISTS + 1):
            custom = self._playlist_titles.get(i, "").strip()
            if custom and custom != self.default_playlist_title(i):
                config[f"playlist{i}_title"] = custom

        if hasattr(self, "file_browser"):
            config["browser_tabs"] = [
                {"title": title, "path": path}
                for title, path in self.file_browser.user_tab_states()
            ]

        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    def collapse_file_browser(self) -> None:
        if self._browser_collapsed:
            return
        self._browser_collapsed = True
        self._apply_browser_collapsed_state(True)
        self.save_config()

    def expand_file_browser(self) -> None:
        if not self._browser_collapsed:
            return
        self._browser_collapsed = False
        self._apply_browser_collapsed_state(False)
        self.save_config()

    def _apply_browser_collapsed_state(self, collapsed: bool) -> None:
        if not hasattr(self, "file_browser") or not hasattr(self, "browser_expand_btn"):
            return
        if collapsed and hasattr(self, "audio_browser_splitter"):
            sizes = self.audio_browser_splitter.sizes()
            if len(sizes) == 2 and sizes[0] > 0:
                self._browser_saved_splitter = sizes
        self.file_browser.setVisible(not collapsed)
        self.browser_expand_btn.setVisible(collapsed)
        if (
            not collapsed
            and self._browser_saved_splitter
            and hasattr(self, "audio_browser_splitter")
        ):
            self.audio_browser_splitter.setSizes(self._browser_saved_splitter)
    def load_playlists(self):
        for path, playlist in zip(self.playlist_paths, self.playlists):
            if path.exists():
                try:
                    load_playlist_from_file(
                        playlist, str(path), timeline_states=self._track_timeline_state
                    )
                except Exception as e:
                    print(f"Error loading playlist: {e}")

    def save_all_playlists(self):
        self._save_current_timeline_state()
        for path, playlist in zip(self.playlist_paths, self.playlists):
            try:
                save_playlist_to_file(playlist, str(path), self._track_timeline_state)
            except Exception as e:
                print(f"Auto-save error: {e}")

    def _playlist_dialog_dir(self) -> str:
        if self._last_playlist_dialog_dir and os.path.isdir(self._last_playlist_dialog_dir):
            return self._last_playlist_dialog_dir
        return str(Path.home())

    def _remember_playlist_dialog_path(self, file_path: str) -> None:
        parent = str(Path(file_path).resolve().parent)
        if os.path.isdir(parent):
            self._last_playlist_dialog_dir = parent

    def export_playlist_widget(self, playlist: PlaylistWidget):
        track_count = playlist.count()
        file_paths = playlist.all_file_paths()

        if track_count == 0:
            QMessageBox.warning(self, "Export", "Playlist is empty.")
            return

        if not file_paths:
            QMessageBox.warning(
                self,
                "Export",
                "Tracks are listed but file paths are missing. "
                "Try re-adding the audio files to the playlist.",
            )
            return

        default_path = os.path.join(
            self._playlist_dialog_dir(),
            f"playlist{playlist.playlist_num}.json",
        )
        playlist_title = self.get_playlist_title(playlist.playlist_num)
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            f"Export {playlist_title}",
            default_path,
            "JSON Files (*.json)",
        )
        if not file_path:
            return

        if not file_path.lower().endswith('.json'):
            file_path += '.json'

        try:
            self._save_current_timeline_state()
            saved_count = save_playlist_to_file(playlist, file_path, self._track_timeline_state)
            self._remember_playlist_dialog_path(file_path)
            QMessageBox.information(
                self,
                "Success",
                f"{playlist_title} exported ({saved_count} track(s))!",
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to export playlist: {e}")

    def import_playlist_widget(self, playlist: PlaylistWidget):
        playlist_title = self.get_playlist_title(playlist.playlist_num)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Import {playlist_title}",
            self._playlist_dialog_dir(),
            "JSON Files (*.json)",
        )
        if not file_path:
            return

        self._remember_playlist_dialog_path(file_path)
        replace = True
        if playlist.count() > 0:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Import Playlist")
            dialog.setText("Replace current playlist or append imported tracks?")
            replace_btn = dialog.addButton("Replace", QMessageBox.ButtonRole.YesRole)
            append_btn = dialog.addButton("Append", QMessageBox.ButtonRole.NoRole)
            cancel_btn = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked is None or clicked == cancel_btn:
                return
            replace = clicked == replace_btn

        try:
            self._save_current_timeline_state()
            added, skipped, missing = load_playlist_from_file(
                playlist,
                file_path,
                clear=replace,
                timeline_states=self._track_timeline_state,
            )
            if added == 0:
                QMessageBox.warning(
                    self,
                    "Import",
                    "No tracks were imported. The file is empty or contains invalid entries.",
                )
                return

            self.duration_prober.probe_playlist(playlist)
            playlist.setCurrentRow(0)
            playlist.scrollToTop()
            playlist.repaint()
            self.refresh_playlist_footer(playlist)
            playlist_index = self.playlists.index(playlist)
            save_playlist_to_file(
                playlist,
                str(self.playlist_paths[playlist_index]),
                self._track_timeline_state,
            )

            if missing > 0 or skipped > 0:
                details = [f"Imported {added} track(s)."]
                if missing > 0:
                    details.append(
                        f"{missing} track(s) were added but audio files were not found on disk."
                    )
                if skipped > 0:
                    details.append(f"{skipped} invalid entr{'y' if skipped == 1 else 'ies'} skipped.")
                QMessageBox.warning(self, "Import", "\n".join(details))
            else:
                QMessageBox.information(self, "Success", f"Imported {added} track(s).")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to import playlist: {e}")

    def add_to_playlist_widget(self, playlist: PlaylistWidget):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Audio Files",
            self._playlist_dialog_dir(),
            "Audio Files (*.mp3 *.wav *.m4a *.flac *.aac)",
        )
        if not files:
            return

        self._remember_playlist_dialog_path(files[0])
        for file_path in files:
            playlist.add_file(file_path)
        self.refresh_playlist_footer(playlist)

    def remove_from_playlist_widget(self, playlist: PlaylistWidget):
        current_row = playlist.currentRow()
        if current_row >= 0:
            playlist.takeItem(current_row)
        self.refresh_playlist_footer(playlist)

    def clear_playlist_widget(self, playlist: PlaylistWidget):
        if playlist.count() == 0:
            return

        answer = QMessageBox.question(
            self,
            "Clear Playlist",
            f"Remove all tracks from {self.get_playlist_title(playlist.playlist_num)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        playlist.clear()
        if self.current_playing_playlist == playlist.playlist_num:
            for media_player in self._players:
                media_player.stop()
            self._cancel_crossfade()
            self.current_playing_playlist = None
            self.current_playing_item = None
            self._current_file_path = None
            self.waveform.set_waveform([])
            self.track_info.setText("No track selected")
            self._reset_bpm_label()
            self._update_file_properties(None)
            self.update_time_display()
        self.refresh_playlist_footer(playlist)

    def update_track_info_playing(self, file_path: str | None = None):
        file_path = file_path or self._current_file_path
        name = self.get_track_display_name(self.current_playing_item, file_path)
        self.track_info.setText(name)

    def update_track_info_ready(self):
        if self.current_playing_item:
            file_path = get_item_file_path(self.current_playing_item)
            name = self.get_track_display_name(self.current_playing_item, file_path)
            self.track_info.setText(name)
            return
        self.track_info.setText("No track selected")

    def play_selected(
        self,
        playlist_num,
        *,
        space_triggered: bool = False,
        preserve_range_latch: bool = False,
    ) -> bool:
        playlist = self.get_playlist(playlist_num)
        current_item = playlist.currentItem()
        if not current_item:
            return False
        self.current_playing_playlist = playlist_num
        self.current_playing_item = current_item
        file_path = get_item_file_path(current_item)
        return self.play_file(
            file_path,
            space_triggered=space_triggered,
            preserve_range_latch=preserve_range_latch,
        )

    @staticmethod
    def _item_is_playable(item) -> bool:
        if item is None:
            return False
        file_path = get_item_file_path(item)
        return bool(file_path) and os.path.isfile(file_path)

    def _save_timeline_for_path(
        self,
        file_path: str,
        waveform: AudioWaveform | None = None,
    ) -> None:
        key = os.path.abspath(file_path)
        wf = waveform or self.waveform
        # Never write another track's on-screen markers onto this path.
        if wf is self.waveform and self._displayed_track_path:
            if os.path.abspath(self._displayed_track_path) != key:
                return
        if wf is self.preview_waveform and self._preview_displayed_track_path:
            if os.path.abspath(self._preview_displayed_track_path) != key:
                return
        state = wf.get_timeline_state()
        self._track_timeline_state[key] = TimelineState(
            range_start_ms=state.range_start_ms,
            range_end_ms=state.range_end_ms,
            fade_in_ms=state.fade_in_ms,
            fade_out_ms=state.fade_out_ms,
        )

    def _save_current_timeline_state(self) -> None:
        if self._displayed_track_path:
            self._save_timeline_for_path(self._displayed_track_path, self.waveform)
        if self._preview_displayed_track_path:
            self._save_timeline_for_path(
                self._preview_displayed_track_path,
                self.preview_waveform,
            )
            return
        key = self._timeline_state_key()
        if key:
            self._track_timeline_state[key] = self.waveform.get_timeline_state()

    def _timeline_state_key(self) -> str | None:
        if self._displayed_track_path:
            return self._displayed_track_path
        if self._current_file_path:
            return os.path.abspath(self._current_file_path)
        _playlist_num, item = self.get_selected_track()
        if item:
            path = get_item_file_path(item)
            if path:
                return os.path.abspath(path)
        return None

    def _default_timeline_state(self, duration: int) -> TimelineState:
        return TimelineState(
            range_start_ms=0,
            range_end_ms=duration if duration > 0 else 0,
            fade_in_ms=0,
            fade_out_ms=0,
        )

    def _restore_timeline_state(self, file_path: str, duration: int) -> None:
        key = os.path.abspath(file_path)
        state = self._track_timeline_state.get(key)
        if state is None:
            self.waveform.set_timeline_state(self._default_timeline_state(duration))
            return
        range_end_ms = state.range_end_ms
        if range_end_ms <= 0 and duration > 0:
            range_end_ms = duration
        self.waveform.set_timeline_state(
            TimelineState(
                range_start_ms=state.range_start_ms,
                range_end_ms=range_end_ms,
                fade_in_ms=state.fade_in_ms,
                fade_out_ms=state.fade_out_ms,
            )
        )

    def _prepare_timeline_for_path(self, file_path: str) -> None:
        """Apply saved trim/fade for the next track before media is loaded."""
        abs_path = os.path.abspath(file_path)
        duration = 0
        if self.current_playing_item is not None:
            duration = get_item_duration(self.current_playing_item) or 0
        if duration > 0:
            self.waveform.set_duration(duration)
            self._restore_timeline_state(abs_path, duration)
            self.waveform.set_position(self._range_start_ms())
            return

        self.waveform.set_duration(0)
        self.waveform.set_timeline_state(TimelineState())
        self.waveform.set_position(0)

    def _load_track_waveform_and_timeline(self, file_path: str) -> None:
        abs_path = os.path.abspath(file_path)
        if self._displayed_track_path and self._displayed_track_path != abs_path:
            self._save_timeline_for_path(self._displayed_track_path)
        self._displayed_track_path = abs_path
        self.waveform.set_waveform([])
        self._request_waveform(file_path)
        duration = get_audio_duration_ms(file_path) or 0
        self.waveform.set_duration(duration)
        self._restore_timeline_state(file_path, duration)
        end_ms = self.waveform.range_end_ms or duration
        pos = self.waveform.position
        if pos < self.waveform.range_start_ms or pos > end_ms:
            self.waveform.set_position(self.waveform.range_start_ms)
        self._update_timeline_labels()

    def _on_waveform_range_changed(self, start_ms: int, end_ms: int) -> None:
        self._save_current_timeline_state()
        pos = self.waveform.position
        if pos < start_ms or pos > end_ms:
            clamped = max(start_ms, min(pos, end_ms))
            self.waveform.set_position(clamped)
            if self.player.source().isValid():
                self.seek(clamped)
        self._update_timeline_labels()

    def _on_waveform_fade_changed(self, fade_in_ms: int, fade_out_ms: int) -> None:
        self._save_current_timeline_state()
        self._fade_out_started = False

    # --- Preview deck ---

    def _update_preview_track_info(self, file_path: str | None = None) -> None:
        file_path = file_path or self._preview_file_path
        name = self.get_track_display_name(self._preview_item, file_path)
        self.preview_track_info.setText(name)

    def load_preview_track(self, playlist_num: int, item) -> None:
        if item is None:
            self._clear_preview_deck()
            return

        file_path = get_item_file_path(item)
        if not file_path or not os.path.isfile(file_path):
            self._preview_playlist_num = playlist_num
            self._preview_item = item
            self._preview_file_path = file_path
            self.preview_track_info.setText(self.get_track_display_name(item, file_path))
            self._update_preview_properties(file_path)
            self._clear_preview_playback()
            self.preview_waveform.set_waveform([])
            self.preview_waveform.set_duration(0)
            self.preview_waveform.set_position(0)
            return

        abs_path = os.path.abspath(file_path)
        same_track = self._preview_displayed_track_path == abs_path
        if (
            self._preview_displayed_track_path
            and self._preview_displayed_track_path != abs_path
        ):
            self._save_timeline_for_path(
                self._preview_displayed_track_path,
                self.preview_waveform,
            )

        self._preview_playlist_num = playlist_num
        self._preview_item = item
        self._preview_file_path = file_path
        self._preview_displayed_track_path = abs_path
        self._preview_range_end_latched = False
        self._preview_fade_out_started = False

        if not same_track:
            self.preview_waveform.reset_view()
            self.preview_waveform.set_waveform([])
        self._prepare_preview_timeline_for_path(file_path)
        self._update_preview_track_info(file_path)
        self._update_preview_properties(file_path)
        width = self.preview_waveform.width()
        if width > 0 and self._try_apply_cached_waveform(
            self.preview_waveform,
            file_path,
            width,
            self._preview_waveform_res_enabled,
        ):
            pass
        elif width <= 0 or not self.preview_waveform.waveform_data:
            QTimer.singleShot(0, lambda path=file_path: self._request_preview_waveform(path))
        if self._preview_autoplay:
            self._start_preview_playback(file_path)
        else:
            self._prepare_preview_source(file_path)
        self._update_preview_timeline_labels()
        self._update_preview_time_display()

    def on_preview_autoplay_toggled(self, checked: bool) -> None:
        self._preview_autoplay = checked

    def _prepare_preview_source(self, file_path: str) -> None:
        abs_path = os.path.abspath(file_path)
        current_source = self._preview_player.source().toLocalFile()
        if current_source and os.path.abspath(current_source) == abs_path:
            if self._preview_player.duration() > 0:
                start_ms = self._preview_playback_start_ms()
                self.preview_waveform.set_position(start_ms)
            return
        self._preview_volume_fader.cancel()
        self._preview_pending_playback_start = False
        self._preview_pending_start_ms = None
        self._preview_seek_target_ms = None
        self._preview_playback_token += 1
        self._preview_player.setSource(QUrl.fromLocalFile(file_path))

    def preview_play(self) -> None:
        if not self._preview_file_path or not os.path.isfile(self._preview_file_path):
            return
        if self._preview_player.source().isEmpty():
            self._start_preview_playback(self._preview_file_path)
            return
        if self._preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._preview_player.duration() > 0:
            start_ms = self._preview_playback_start_ms()
            pos = self._preview_player.position()
            range_end = self._preview_range_end_ms()
            if pos < start_ms or (range_end > 0 and pos >= range_end):
                self.preview_waveform.set_position(start_ms)
                self._preview_seek_target_ms = start_ms if start_ms > 0 else None
                self._preview_player.setPosition(start_ms)
            else:
                self._preview_seek_target_ms = None
        self._set_preview_output_volume(self._preview_playback_volume)
        self._preview_player.play()
        self._preview_fade_out_started = False
        self._apply_preview_fade_in()
        self._update_preview_time_display()

    def preview_pause(self) -> None:
        self._preview_volume_fader.cancel()
        self._set_preview_output_volume(self._preview_playback_volume)
        self._preview_player.pause()
        self._update_preview_time_display()

    def preview_stop(self) -> None:
        self._preview_volume_fader.cancel()
        self._preview_range_end_latched = False
        self._preview_fade_out_started = False
        self._preview_player.stop()
        if self._preview_player.duration() > 0:
            start_ms = self._preview_range_start_ms()
            self.preview_waveform.set_position(start_ms)
        self._set_preview_output_volume(self._preview_playback_volume)
        self._update_preview_time_display()

    def clear_preview_deck(self) -> None:
        """Eject the playlist PREVIEW deck (public alias)."""
        self._clear_preview_deck()

    def clear_air_deck(self) -> None:
        """Eject / clear the ON AIR deck."""
        self._clear_air_deck()

    def _clear_air_deck(self) -> None:
        if self._current_file_path:
            self._save_timeline_for_path(self._current_file_path)
        self._cancel_crossfade()
        self.volume_fader.cancel()
        self._stop_fade_active = False
        self._stop_fade_path = None
        self._stop_fade_item = None
        self._stop_fade_playlist_num = None
        self._queued_after_stop_fade = None
        self._range_end_latched = False
        self._suppress_range_end = False
        self._pending_transition_fade_ms = 0
        self._fade_out_started = False
        self._cancel_pending_playback()
        self._playback_token += 1
        for media_player in self._players:
            media_player.stop()
        self._set_output_volume(self._playback_volume)
        self.current_playing_playlist = None
        self.current_playing_item = None
        self._current_file_path = None
        self._displayed_track_path = None
        self.waveform.set_waveform([])
        self.waveform.set_duration(0)
        self.waveform.set_position(0)
        self.track_info.setText("No track selected")
        self._reset_bpm_label()
        self._update_file_properties(None)
        self._clear_air_spectrum()
        self.update_time_display()

    def _clear_preview_deck(self) -> None:
        if self._preview_displayed_track_path:
            self._save_timeline_for_path(
                self._preview_displayed_track_path,
                self.preview_waveform,
            )
        self._preview_playlist_num = None
        self._preview_item = None
        self._preview_file_path = None
        self._preview_displayed_track_path = None
        self._clear_preview_playback()
        self.preview_waveform.set_waveform([])
        self.preview_waveform.set_duration(0)
        self.preview_waveform.set_position(0)
        self.preview_track_info.setText("No track selected")
        self._update_preview_properties(None)
        self._update_preview_time_display()

    def clear_browser_preview(self) -> None:
        """Eject / clear the browser preview deck."""
        self._browser_file_path = None
        self._browser_displayed_track_path = None
        self._browser_pending_playback_start = False
        self._browser_pending_start_ms = None
        self._browser_seek_target_ms = None
        self._browser_playback_token += 1
        self._browser_player.stop()
        self._browser_player.setSource(QUrl())
        self.browser_waveform.set_waveform([])
        self.browser_waveform.set_duration(0)
        self.browser_waveform.set_position(0)
        self.browser_track_info.setText("No track selected")
        self._update_browser_time_display()

    def _clear_preview_playback(self) -> None:
        self._preview_volume_fader.cancel()
        self._preview_pending_playback_start = False
        self._preview_pending_start_ms = None
        self._preview_seek_target_ms = None
        self._preview_pending_playback_token = 0
        self._preview_player.stop()

    def _prepare_preview_timeline_for_path(self, file_path: str) -> None:
        abs_path = os.path.abspath(file_path)
        duration = 0
        if self._preview_item is not None:
            duration = get_item_duration(self._preview_item) or 0
        if duration > 0:
            self.preview_waveform.set_duration(duration)
            self._restore_timeline_state_for_waveform(
                abs_path,
                duration,
                self.preview_waveform,
            )
            self.preview_waveform.set_position(self._preview_range_start_ms())
            return

        self.preview_waveform.set_duration(0)
        self.preview_waveform.set_timeline_state(TimelineState())
        self.preview_waveform.set_position(0)

    def _restore_timeline_state_for_waveform(
        self,
        file_path: str,
        duration: int,
        waveform: AudioWaveform,
    ) -> None:
        key = os.path.abspath(file_path)
        state = self._track_timeline_state.get(key)
        if state is None:
            waveform.set_timeline_state(self._default_timeline_state(duration))
            return
        range_end_ms = state.range_end_ms
        if range_end_ms <= 0 and duration > 0:
            range_end_ms = duration
        waveform.set_timeline_state(
            TimelineState(
                range_start_ms=state.range_start_ms,
                range_end_ms=range_end_ms,
                fade_in_ms=state.fade_in_ms,
                fade_out_ms=state.fade_out_ms,
            )
        )

    def _preview_range_start_ms(self) -> int:
        return self.preview_waveform.range_start_ms

    def _preview_range_end_ms(self) -> int:
        end = self.preview_waveform.range_end_ms
        duration = self._preview_player.duration()
        if end <= 0 and duration > 0:
            return duration
        return end

    def _preview_fade_in_ms(self) -> int:
        return self.preview_waveform.fade_in_ms

    def _preview_fade_out_ms(self) -> int:
        return self.preview_waveform.fade_out_ms

    def _preview_playback_start_ms(self) -> int:
        start_ms = self._preview_range_start_ms()
        playhead_ms = self.preview_waveform.position
        range_end = self._preview_range_end_ms()
        if range_end > 0 and start_ms <= playhead_ms < range_end:
            return playhead_ms
        return start_ms

    def _preview_volume_for_position(self, position: int) -> float:
        if not self._timeline_fades_enabled():
            return self._preview_playback_volume
        start = self._preview_range_start_ms()
        end = self._preview_range_end_ms()
        fade_in = self._preview_fade_in_ms()
        fade_out = self._preview_fade_out_ms()

        if fade_in > 0 and position < start + fade_in:
            progress = (position - start) / fade_in
            return self._preview_playback_volume * max(0.0, min(1.0, progress))
        if fade_out > 0 and position > end - fade_out:
            progress = (end - position) / fade_out
            return self._preview_playback_volume * max(0.0, min(1.0, progress))
        return self._preview_playback_volume

    def _apply_preview_timeline_volume(self, position: int) -> None:
        if not self._timeline_fades_enabled():
            if (
                not self._preview_volume_fader.is_active
                and self._preview_player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
            ):
                self._set_preview_output_volume(self._preview_playback_volume)
            self._preview_fade_out_started = False
            return
        if self._preview_volume_fader.is_active:
            return
        if self._preview_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return

        fade_in = self._preview_fade_in_ms()
        fade_out = self._preview_fade_out_ms()
        if fade_in <= 0 and fade_out <= 0:
            self._set_preview_output_volume(self._preview_playback_volume)
            self._preview_fade_out_started = False
            return

        self._preview_volume_fader.cancel()
        self._set_preview_output_volume(self._preview_volume_for_position(position))
        end = self._preview_range_end_ms()
        self._preview_fade_out_started = fade_out > 0 and position >= end - fade_out

    def _apply_preview_fade_in(self) -> None:
        position = self._preview_player.position()
        self._preview_fade_out_started = False
        if (
            self._timeline_fades_enabled()
            and (self._preview_fade_in_ms() > 0 or self._preview_fade_out_ms() > 0)
        ):
            self._apply_preview_timeline_volume(position)
        else:
            self._preview_volume_fader.cancel()
            self._set_preview_output_volume(self._preview_playback_volume)

    def _start_preview_playback(self, file_path: str) -> None:
        abs_path = os.path.abspath(file_path)
        current_source = self._preview_player.source().toLocalFile()
        switching = (
            current_source
            and os.path.abspath(current_source) != abs_path
            and self._preview_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )

        def begin_preview():
            self._preview_volume_fader.cancel()
            self._preview_pending_playback_start = True
            self._preview_playback_token += 1
            self._preview_pending_playback_token = self._preview_playback_token
            self._preview_pending_start_ms = self._preview_playback_start_ms()
            self.preview_waveform.set_position(self._preview_pending_start_ms)
            self._preview_player.setSource(QUrl.fromLocalFile(file_path))
            self._set_preview_output_volume(self._preview_playback_volume)

        if switching:
            self._preview_player.stop()
        begin_preview()

    def _try_begin_preview_playback(self) -> None:
        if not self._preview_pending_playback_start or self._preview_pending_start_ms is None:
            return
        if self._preview_pending_playback_token != self._preview_playback_token:
            return
        if not self._preview_file_path:
            return
        source_path = self._preview_player.source().toLocalFile()
        if not source_path:
            return
        if os.path.abspath(source_path) != os.path.abspath(self._preview_file_path):
            return
        if self._preview_player.mediaStatus() not in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            return

        start_ms = self._preview_pending_start_ms
        self._preview_pending_playback_start = False
        self._preview_pending_start_ms = None
        self._preview_range_end_latched = False
        self.preview_waveform.set_position(start_ms)
        self._preview_seek_target_ms = start_ms if start_ms > 0 else None
        self._preview_player.setPosition(start_ms)
        self._set_preview_output_volume(self._preview_playback_volume)
        self._preview_player.play()
        self._preview_fade_out_started = False
        self._apply_preview_fade_in()
        self._update_preview_time_display()

    def _stop_preview_for_air(self) -> None:
        self._preview_volume_fader.cancel()
        self._preview_range_end_latched = False
        self._preview_fade_out_started = False
        self._preview_pending_playback_start = False
        self._preview_pending_start_ms = None
        self._preview_seek_target_ms = None
        self._preview_player.stop()
        start_ms = self._preview_range_start_ms()
        self.preview_waveform.set_position(start_ms)
        self._set_preview_output_volume(self._preview_playback_volume)
        self._update_preview_time_display()

    def take_preview_to_air(self, *, space_triggered: bool = False) -> bool:
        self._pending_space_restart = None
        if not self._preview_file_path or not self._preview_item:
            return False
        if not os.path.isfile(self._preview_file_path):
            return False

        self._save_timeline_for_path(
            self._preview_displayed_track_path or self._preview_file_path,
            self.preview_waveform,
        )

        air_start_ms = self._preview_range_start_ms()
        self._stop_preview_for_air()

        self.current_playing_playlist = self._preview_playlist_num
        self.current_playing_item = self._preview_item

        return self.play_file(
            self._preview_file_path,
            space_triggered=space_triggered,
            start_ms=air_start_ms,
            force_restart=True,
        )

    def seek_preview(self, position: int) -> None:
        self._preview_range_end_latched = False
        duration = self._preview_player.duration()
        if duration <= 0:
            return
        start_ms = self._preview_range_start_ms()
        end_ms = self._preview_range_end_ms()
        position = max(start_ms, min(position, end_ms))
        if position >= 0 and position <= duration:
            self._preview_player.setPosition(position)
            self.preview_waveform.set_position(position)
            self._preview_fade_out_started = False
            if self._preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._apply_preview_timeline_volume(position)
            else:
                self._preview_volume_fader.cancel()
                if self._timeline_fades_enabled() and (
                    self._preview_fade_in_ms() > 0 or self._preview_fade_out_ms() > 0
                ):
                    self._set_preview_output_volume(self._preview_volume_for_position(position))
                else:
                    self._set_preview_output_volume(self._preview_playback_volume)
            self._update_preview_time_display()

    def reset_preview_waveform_zoom(self) -> None:
        self.preview_waveform.reset_view()

    def rewind_preview_to_start(self) -> None:
        if self._preview_player.duration() <= 0 and not self._preview_file_path:
            return
        start_ms = self._preview_range_start_ms()
        was_playing = (
            self._preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        self._preview_range_end_latched = False
        self._preview_fade_out_started = False
        if self._preview_player.duration() > 0:
            self._preview_player.setPosition(start_ms)
            self._preview_seek_target_ms = start_ms if start_ms > 0 else None
        self.preview_waveform.set_position(start_ms)
        self._preview_volume_fader.cancel()
        if was_playing:
            self._apply_preview_timeline_volume(start_ms)
        elif self._timeline_fades_enabled():
            self._set_preview_output_volume(self._preview_volume_for_position(start_ms))
        else:
            self._set_preview_output_volume(self._preview_playback_volume)
        self._update_preview_time_display()

    def on_preview_waveform_res_toggled(self, checked: bool) -> None:
        self._preview_waveform_res_enabled = checked
        if self._preview_file_path and (
            self.preview_waveform.is_zoomed or not checked
        ):
            self._request_preview_waveform(
                self._preview_file_path,
                self.preview_waveform.width(),
            )

    def _refresh_preview_res_waveform(self) -> None:
        if (
            self._preview_file_path
            and self.preview_waveform.is_zoomed
            and self._preview_waveform_res_enabled
        ):
            self._request_preview_waveform(
                self._preview_file_path,
                self.preview_waveform.width(),
            )

    def _on_preview_waveform_view_changed(self) -> None:
        zoomed = self.preview_waveform.is_zoomed
        zoom_changed = zoomed != self._preview_waveform_was_zoomed
        self._preview_waveform_was_zoomed = zoomed

        if hasattr(self, "btn_preview_zoom_reset"):
            self.btn_preview_zoom_reset.setEnabled(zoomed)
        if hasattr(self, "btn_preview_waveform_res"):
            self.btn_preview_waveform_res.setEnabled(zoomed)
            if not zoomed and self.btn_preview_waveform_res.isChecked():
                self.btn_preview_waveform_res.blockSignals(True)
                self.btn_preview_waveform_res.setChecked(False)
                self.btn_preview_waveform_res.blockSignals(False)
                self._preview_waveform_res_enabled = False

        if not self._preview_file_path:
            return
        if zoomed and self._preview_waveform_res_enabled:
            self._preview_res_waveform_timer.start(120)
        elif zoom_changed and not zoomed:
            self._request_preview_waveform(
                self._preview_file_path,
                self.preview_waveform.width(),
            )

    def regenerate_preview_waveform(self, width: int) -> None:
        if self._is_window_resizing or not self._preview_file_path or width <= 0:
            return
        self._request_preview_waveform(self._preview_file_path, width)

    def _request_preview_waveform(self, file_path: str, width: int | None = None) -> None:
        if not file_path:
            return
        if width is None:
            width = self.preview_waveform.width()
        if width <= 0:
            return
        if self._try_apply_cached_waveform(
            self.preview_waveform,
            file_path,
            width,
            self._preview_waveform_res_enabled,
        ):
            return
        num_bars, start_ms, end_ms = self._waveform_load_params(
            self.preview_waveform,
            file_path,
            width,
            self._preview_waveform_res_enabled,
        )
        self._preview_waveform_request_id = self.preview_waveform_loader.request(
            file_path,
            num_bars,
            start_ms=start_ms,
            end_ms=end_ms,
        )

    def _on_preview_waveform_ready(
        self,
        file_path: str,
        data: list,
        request_id: int,
        start_ms: int,
        end_ms: int,
    ) -> None:
        if request_id != self._preview_waveform_request_id:
            return
        if not self._preview_file_path:
            return
        if os.path.abspath(file_path) != os.path.abspath(self._preview_file_path):
            return
        self._store_waveform_cache(file_path, data, start_ms, end_ms)
        if (start_ms == 0 and end_ms == 0) and data:
            from app.analysis_cache import (
                CANONICAL_WAVEFORM_BARS,
                has_waveform_cache,
                load_bpm,
                resample_peaks,
                save_analysis,
            )

            if not has_waveform_cache(file_path):
                peaks = (
                    data
                    if len(data) >= CANONICAL_WAVEFORM_BARS
                    else resample_peaks(data, CANONICAL_WAVEFORM_BARS)
                )
                save_analysis(file_path, bpm=load_bpm(file_path), peaks=peaks, num_bars=len(peaks))
        if start_ms > 0 or end_ms > start_ms:
            self.preview_waveform.set_waveform(
                data,
                map_start_ms=start_ms,
                map_end_ms=end_ms,
            )
        else:
            self.preview_waveform.set_waveform(data)

    def _on_preview_waveform_range_changed(self, start_ms: int, end_ms: int) -> None:
        if self._preview_displayed_track_path:
            self._save_timeline_for_path(
                self._preview_displayed_track_path,
                self.preview_waveform,
            )
        pos = self.preview_waveform.position
        if pos < start_ms or pos > end_ms:
            clamped = max(start_ms, min(pos, end_ms))
            self.preview_waveform.set_position(clamped)
            if self._preview_player.source().isValid():
                self.seek_preview(clamped)
        self._update_preview_timeline_labels()

    def _on_preview_waveform_fade_changed(self, fade_in_ms: int, fade_out_ms: int) -> None:
        if self._preview_displayed_track_path:
            self._save_timeline_for_path(
                self._preview_displayed_track_path,
                self.preview_waveform,
            )
        self._preview_fade_out_started = False

    def _update_preview_timeline_labels(self) -> None:
        duration = self._preview_player.duration()
        if duration > 0:
            self.preview_timeline_end_label.setText(format_time_ms(duration))
        self.preview_timeline_start_label.setText(
            format_time_ms(self._preview_range_start_ms())
        )

    def _update_preview_time_display(self) -> None:
        duration = self._preview_player.duration()
        position = self._preview_player.position()
        if duration <= 0:
            self.preview_timeline_start_label.setText("0:00")
            self.preview_timeline_end_label.setText("0:00")
            return
        self.preview_timeline_start_label.setText(
            format_time_ms(self._preview_range_start_ms())
        )
        self.preview_timeline_end_label.setText(format_time_ms(duration))

    def _handle_preview_range_end(self) -> None:
        if self._preview_handling_range_end:
            return
        self._preview_handling_range_end = True
        try:
            self._preview_volume_fader.cancel()
            self._preview_player.pause()
            self._preview_player.setPosition(self._preview_range_end_ms())
            self._preview_range_end_latched = True
        finally:
            self._preview_handling_range_end = False

    def _check_preview_fade_out_and_range(self, position: int) -> None:
        if self._preview_range_end_latched:
            return
        if self._preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._apply_preview_timeline_volume(position)
        range_end = self._preview_range_end_ms()
        if position >= range_end and range_end > 0:
            if self._preview_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            self._handle_preview_range_end()

    def _on_preview_position_changed(self, position: int) -> None:
        if (
            self._preview_seek_target_ms is not None
            and self._preview_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            if abs(position - self._preview_seek_target_ms) > 50:
                self._preview_player.setPosition(self._preview_seek_target_ms)
            else:
                self._preview_seek_target_ms = None

        if not self._is_window_resizing and not self.preview_waveform.is_scrubbing:
            self.preview_waveform.set_position(position)
        self._check_preview_fade_out_and_range(position)
        self._update_preview_time_display()

    def _on_preview_duration_changed(self, duration: int) -> None:
        if duration <= 0 and self._preview_pending_playback_start:
            return
        self.preview_waveform.set_duration(duration)
        if self._preview_file_path and duration > 0 and not self._preview_pending_playback_start:
            self._restore_timeline_state_for_waveform(
                self._preview_file_path,
                duration,
                self.preview_waveform,
            )
        if self._preview_item and duration > 0:
            set_item_duration(self._preview_item, duration)
        self._update_preview_time_display()
        self._update_preview_timeline_labels()

    def _on_preview_media_status_changed(self, status) -> None:
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            duration = self._preview_player.duration()
            if duration > 0 and self._preview_pending_playback_start:
                self.preview_waveform.set_duration(duration)
                self._restore_timeline_state_for_waveform(
                    self._preview_file_path or "",
                    duration,
                    self.preview_waveform,
                )
                self._preview_pending_start_ms = self._preview_playback_start_ms()
                self.preview_waveform.set_position(self._preview_pending_start_ms)
            self._try_begin_preview_playback()
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._preview_player.setPosition(self._preview_range_start_ms())
            self._preview_fade_out_started = False

    def _on_preview_playback_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            if self._preview_volume_fader.is_active:
                self._apply_preview_output_volume(self._preview_volume_fader.current_volume)
            else:
                self._apply_preview_output_volume(self._preview_playback_volume)

    # --- Browser file preview (no range/fade markers) ---

    def load_browser_preview(self, file_path: str) -> None:
        if not file_path or not is_audio_file(file_path) or not os.path.isfile(file_path):
            return

        abs_path = os.path.abspath(file_path)
        same_track = self._browser_displayed_track_path == abs_path
        self._browser_file_path = file_path
        self._browser_displayed_track_path = abs_path
        self.browser_track_info.setText(os.path.basename(file_path))

        if not same_track:
            self.browser_waveform.reset_view()
            self.browser_waveform.set_waveform([])
            self.browser_waveform.set_duration(0)
            self.browser_waveform.set_position(0)
            self.browser_waveform.reset_timeline()

        duration = get_audio_duration_ms(file_path) or 0
        if duration > 0:
            self.browser_waveform.set_duration(duration)
            self.browser_waveform.reset_timeline()

        width = self.browser_waveform.width()
        if width > 0 and self._try_apply_cached_waveform(
            self.browser_waveform,
            file_path,
            width,
            False,
            store=False,
        ):
            pass
        elif width <= 0 or not self.browser_waveform.waveform_data:
            QTimer.singleShot(0, lambda path=file_path: self._request_browser_waveform(path))

        if self._browser_autoplay:
            self._start_browser_playback(file_path)
        else:
            self._prepare_browser_source(file_path)
        self._update_browser_time_display()

    def on_browser_autoplay_toggled(self, checked: bool) -> None:
        self._browser_autoplay = checked

    def _prepare_browser_source(self, file_path: str) -> None:
        abs_path = os.path.abspath(file_path)
        current_source = self._browser_player.source().toLocalFile()
        if current_source and os.path.abspath(current_source) == abs_path:
            return
        self._browser_pending_playback_start = False
        self._browser_pending_start_ms = None
        self._browser_seek_target_ms = None
        self._browser_playback_token += 1
        self._browser_player.setSource(QUrl.fromLocalFile(file_path))

    def browser_play(self) -> None:
        if not self._browser_file_path or not os.path.isfile(self._browser_file_path):
            return
        if self._browser_player.source().isEmpty():
            self._start_browser_playback(self._browser_file_path)
            return
        if self._browser_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._browser_player.duration() > 0:
            pos = self._browser_player.position()
            duration = self._browser_player.duration()
            start_ms = self.browser_waveform.position
            if pos <= 0 or pos >= duration:
                start_ms = 0
                self.browser_waveform.set_position(0)
                self._browser_seek_target_ms = None
                self._browser_player.setPosition(0)
            elif start_ms > 0 and abs(pos - start_ms) > 50:
                self._browser_seek_target_ms = start_ms
                self._browser_player.setPosition(start_ms)
            else:
                self._browser_seek_target_ms = None
        self._apply_browser_output_volume(self._browser_playback_volume)
        self._browser_player.play()
        self._update_browser_time_display()

    def browser_pause(self) -> None:
        self._browser_player.pause()
        self._update_browser_time_display()

    def browser_stop(self) -> None:
        self._browser_player.stop()
        self.browser_waveform.set_position(0)
        self._apply_browser_output_volume(self._browser_playback_volume)
        self._update_browser_time_display()

    def seek_browser(self, position: int) -> None:
        if self._browser_player.source().isEmpty():
            return
        duration = self._browser_player.duration()
        if duration > 0:
            position = max(0, min(position, duration))
        self._browser_seek_target_ms = position if position > 0 else None
        self._browser_player.setPosition(position)
        self.browser_waveform.set_position(position)

    def rewind_browser_to_start(self) -> None:
        self.seek_browser(0)
        if self._browser_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._update_browser_time_display()

    def regenerate_browser_waveform(self, width: int) -> None:
        if self._browser_file_path and width > 0:
            self._request_browser_waveform(self._browser_file_path, width)

    def _request_browser_waveform(self, file_path: str, width: int | None = None) -> None:
        if not file_path:
            return
        if width is None:
            width = self.browser_waveform.width()
        if width <= 0:
            return
        if self._try_apply_cached_waveform(
            self.browser_waveform,
            file_path,
            width,
            False,
            store=False,
        ):
            return
        num_bars, start_ms, end_ms = self._waveform_load_params(
            self.browser_waveform,
            file_path,
            width,
            False,
        )
        self._browser_waveform_request_id = self.browser_waveform_loader.request(
            file_path,
            num_bars,
            start_ms=start_ms,
            end_ms=end_ms,
        )

    def _on_browser_waveform_ready(
        self,
        file_path: str,
        data: list,
        request_id: int,
        start_ms: int,
        end_ms: int,
    ) -> None:
        if request_id != self._browser_waveform_request_id:
            return
        if not self._browser_file_path:
            return
        if os.path.abspath(file_path) != os.path.abspath(self._browser_file_path):
            return
        # Browser preview is ephemeral — do not write waveform caches
        if start_ms > 0 or end_ms > start_ms:
            self.browser_waveform.set_waveform(
                data,
                map_start_ms=start_ms,
                map_end_ms=end_ms,
            )
        else:
            self.browser_waveform.set_waveform(data)

    def _start_browser_playback(self, file_path: str) -> None:
        self._browser_pending_playback_start = True
        self._browser_playback_token += 1
        self._browser_pending_playback_token = self._browser_playback_token
        start_ms = self.browser_waveform.position if self.browser_waveform.position > 0 else 0
        self._browser_pending_start_ms = start_ms
        self.browser_waveform.set_position(start_ms)
        self._apply_browser_output_volume(self._browser_playback_volume)
        current_source = self._browser_player.source().toLocalFile()
        if current_source and os.path.abspath(current_source) == os.path.abspath(file_path):
            self._try_begin_browser_playback()
            return
        self._browser_player.setSource(QUrl.fromLocalFile(file_path))

    def _try_begin_browser_playback(self) -> None:
        if not self._browser_pending_playback_start:
            return
        if self._browser_pending_playback_token != self._browser_playback_token:
            return
        if not self._browser_file_path:
            return
        source_path = self._browser_player.source().toLocalFile()
        if not source_path:
            return
        if os.path.abspath(source_path) != os.path.abspath(self._browser_file_path):
            return
        # Don't require duration > 0 here — some formats report duration later.
        # Media must be at least loaded/buffered so play() actually starts.
        if self._browser_player.mediaStatus() not in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
        ):
            return

        duration = self._browser_player.duration()
        self._browser_pending_playback_start = False
        start_ms = self._browser_pending_start_ms or 0
        if duration > 0:
            start_ms = max(0, min(start_ms, duration))
            self.browser_waveform.set_duration(duration)
            self.browser_waveform.reset_timeline()
        self._browser_pending_start_ms = None
        self.browser_waveform.set_position(start_ms)
        if start_ms > 0:
            self._browser_seek_target_ms = start_ms
            self._browser_player.setPosition(start_ms)
        self._apply_browser_output_volume(self._browser_playback_volume)
        self._browser_player.play()
        self._update_browser_time_display()

    def _update_browser_time_display(self) -> None:
        duration = self._browser_player.duration()
        if duration <= 0:
            duration = self.browser_waveform.duration
        position = self.browser_waveform.position
        self.browser_timeline_start_label.setText(format_time_ms(position))
        self.browser_timeline_end_label.setText(format_time_ms(duration if duration > 0 else 0))

    def _on_browser_position_changed(self, position: int) -> None:
        if (
            self._browser_seek_target_ms is not None
            and self._browser_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            if abs(position - self._browser_seek_target_ms) > 50:
                self._browser_player.setPosition(self._browser_seek_target_ms)
            else:
                self._browser_seek_target_ms = None

        if not self._is_window_resizing and not self.browser_waveform.is_scrubbing:
            self.browser_waveform.set_position(position)
        self._update_browser_time_display()

    def _on_browser_duration_changed(self, duration: int) -> None:
        if duration > 0:
            self.browser_waveform.set_duration(duration)
            self.browser_waveform.reset_timeline()
            # LoadedMedia can arrive before duration — retry pending autoplay here.
            self._try_begin_browser_playback()
        self._update_browser_time_display()

    def _on_browser_media_status_changed(self, status) -> None:
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
        ):
            duration = self._browser_player.duration()
            if duration > 0:
                self.browser_waveform.set_duration(duration)
                self.browser_waveform.reset_timeline()
            self._try_begin_browser_playback()
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._browser_player.setPosition(0)
            self.browser_waveform.set_position(0)
            self._update_browser_time_display()

    def _on_browser_playback_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._apply_browser_output_volume(self._browser_playback_volume)

    def _fade_in_ms(self) -> int:
        return self.waveform.fade_in_ms

    def _fade_out_ms(self) -> int:
        return self.waveform.fade_out_ms

    def _range_start_ms(self) -> int:
        return self.waveform.range_start_ms

    def _range_end_ms(self) -> int:
        end = self.waveform.range_end_ms
        duration = self.player.duration()
        if end <= 0 and duration > 0:
            return duration
        return end

    def _space_fade_duration_ms(self) -> int:
        return self.fade_duration_spin.value()

    def _apply_fade_in(self) -> None:
        position = self.player.position()
        self._fade_out_started = False
        if (
            self._timeline_fades_enabled()
            and (self._fade_in_ms() > 0 or self._fade_out_ms() > 0)
        ):
            self._apply_timeline_volume(position)
        else:
            self.volume_fader.cancel()
            self._set_output_volume(self._playback_volume)

    def _volume_for_position(self, position: int) -> float:
        if not self._timeline_fades_enabled():
            return self._playback_volume
        start = self._range_start_ms()
        end = self._range_end_ms()
        fade_in = self._fade_in_ms()
        fade_out = self._fade_out_ms()

        if fade_in > 0 and position < start + fade_in:
            progress = (position - start) / fade_in
            return self._playback_volume * max(0.0, min(1.0, progress))
        if fade_out > 0 and position > end - fade_out:
            progress = (end - position) / fade_out
            return self._playback_volume * max(0.0, min(1.0, progress))
        return self._playback_volume

    def _apply_timeline_volume(self, position: int) -> None:
        if not self._timeline_fades_enabled():
            if (
                not self._crossfade_active
                and not self.volume_fader.is_active
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            ):
                self._set_output_volume(self._playback_volume)
            self._fade_out_started = False
            return
        if self._crossfade_active or self.volume_fader.is_active:
            return
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return

        fade_in = self._fade_in_ms()
        fade_out = self._fade_out_ms()
        if fade_in <= 0 and fade_out <= 0:
            self._set_output_volume(self._playback_volume)
            self._fade_out_started = False
            return

        self.volume_fader.cancel()
        self._set_output_volume(self._volume_for_position(position))

        end = self._range_end_ms()
        self._fade_out_started = fade_out > 0 and position >= end - fade_out

    def _apply_fade_out(
        self,
        on_complete=None,
        *,
        use_spinbox: bool = False,
        duration_ms: int | None = None,
    ) -> None:
        fade_ms = duration_ms
        if fade_ms is None:
            fade_ms = self._space_fade_duration_ms() if use_spinbox else self._fade_out_ms()
        if fade_ms > 0 and (duration_ms is not None or use_spinbox or self._crossfade_active):
            self._set_output_volume(self._playback_volume)
        self.volume_fader.cancel()
        if fade_ms > 0:
            self.volume_fader.fade_to(0.0, fade_ms, on_complete)
        elif on_complete:
            on_complete()
        elif not self._is_crossfade_mode():
            self._set_output_volume(0.0)

    def _transition_fade_ms(self, space_triggered: bool, *, overlap: bool = False) -> int:
        if space_triggered or overlap or self._is_crossfade_mode():
            return self._space_fade_duration_ms()
        return self._fade_out_ms()

    def _fade_duration_ms(self) -> int:
        return self._fade_out_ms()

    def _is_playback_active(self) -> bool:
        return (
            self.player.playbackState() in (
                QMediaPlayer.PlaybackState.PlayingState,
                QMediaPlayer.PlaybackState.PausedState,
            )
            and self.player.source().isValid()
        )

    def _playback_start_ms(self) -> int:
        start_ms = self._range_start_ms()
        playhead_ms = self.waveform.position
        range_end = self._range_end_ms()
        if range_end > 0 and start_ms <= playhead_ms < range_end:
            return playhead_ms
        return start_ms

    def _media_ready_for_seek(self, player_idx: int | None = None) -> bool:
        idx = self._active_player_idx if player_idx is None else player_idx
        return self._players[idx].mediaStatus() in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        )

    def _air_player_has_loaded_source(self, player_idx: int, abs_path: str) -> bool:
        source_path = self._players[player_idx].source().toLocalFile()
        if not source_path:
            return False
        if os.path.abspath(source_path) != abs_path:
            return False
        return self._players[player_idx].mediaStatus() in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.LoadingMedia,
        )

    def _pending_source_matches(self, player_idx: int | None = None) -> bool:
        if not self._current_file_path:
            return False
        idx = self._active_player_idx if player_idx is None else player_idx
        source_path = self._players[idx].source().toLocalFile()
        if not source_path:
            return False
        return os.path.abspath(source_path) == os.path.abspath(self._current_file_path)

    def _cancel_pending_playback(self) -> None:
        self._pending_playback_start = False
        self._pending_start_ms = None
        self._seek_target_ms = None
        self._pending_playback_token = 0
        self._pending_playback_player_idx = None

    def _crossfade_fade_ms(self) -> int:
        return self._pending_transition_fade_ms or self._space_fade_duration_ms()

    def _start_playback_at(self, start_ms: int, player_idx: int | None = None) -> None:
        idx = self._active_player_idx if player_idx is None else player_idx
        media_player = self._players[idx]

        if self._crossfade_active and idx == self._crossfade_incoming_idx:
            self._active_player_idx = idx
            self._range_end_latched = False
            self.waveform.set_position(start_ms)
            self._seek_target_ms = start_ms if start_ms > 0 else None
            media_player.setPosition(start_ms)
            self._apply_player_volume(idx, 0.0)
            self._incoming_fader.sync_volume(0.0)
            media_player.play()
            fade_ms = self._crossfade_fade_in_ms
            if fade_ms > 0:
                self._incoming_fader.fade_to(
                    self._playback_volume,
                    fade_ms,
                    on_complete=self._finish_crossfade,
                )
            else:
                self._apply_player_volume(idx, self._playback_volume)
                self._finish_crossfade()
            self._fade_out_started = False
            self._pending_transition_fade_ms = 0
            self._pending_playback_player_idx = None
            self._suppress_range_end = False
            self.update_time_display()
            return

        self._range_end_latched = False
        self.waveform.set_position(start_ms)
        self._seek_target_ms = start_ms if start_ms > 0 else None
        media_player.setPosition(start_ms)
        self._set_output_volume(self._playback_volume)
        media_player.play()
        QTimer.singleShot(0, self._reapply_output_volume)
        self._fade_out_started = False
        self._apply_fade_in()
        self._pending_transition_fade_ms = 0
        self._pending_playback_player_idx = None
        self._suppress_range_end = False
        self.update_time_display()

    def _try_begin_pending_playback(self) -> None:
        pending_idx = self._pending_playback_player_idx
        if pending_idx is None:
            pending_idx = self._active_player_idx
        if not self._pending_playback_start or self._pending_start_ms is None:
            return
        if self._pending_playback_token != self._playback_token:
            return
        if (
            self._players[pending_idx].source().isEmpty()
            or not self._media_ready_for_seek(pending_idx)
            or not self._pending_source_matches(pending_idx)
        ):
            return

        start_ms = self._pending_start_ms
        self._pending_playback_start = False
        self._pending_start_ms = None
        self._start_playback_at(start_ms, player_idx=pending_idx)

    def _start_pending_playback(self, duration: int, player_idx: int) -> None:
        if not self._pending_playback_start or not self._current_file_path:
            return
        self.waveform.set_duration(duration)
        self._restore_timeline_state(self._current_file_path, duration)
        if self._pending_start_ms is None:
            self._pending_start_ms = self._range_start_ms()
        self.waveform.set_position(self._pending_start_ms)
        self._try_begin_pending_playback()

    def _resume_with_fade(self):
        if self._current_file_path and self.player.duration() > 0:
            self._restore_timeline_state(self._current_file_path, self.player.duration())
        start_ms = self._playback_start_ms()
        pos = self.player.position()
        range_end = self._range_end_ms()
        if pos < start_ms or (range_end > 0 and pos >= range_end):
            self.waveform.set_position(start_ms)
            self._seek_target_ms = start_ms if start_ms > 0 else None
            self.player.setPosition(start_ms)
        else:
            self._seek_target_ms = None
        self.player.play()
        self.update_track_info_playing()
        self._fade_out_started = False
        self._apply_fade_in()

    def play_file(
        self,
        file_path,
        *,
        space_triggered: bool = False,
        preserve_range_latch: bool = False,
        start_ms: int | None = None,
        force_restart: bool = False,
        overlap: bool = False,
    ) -> bool:
        if not file_path or not os.path.isfile(file_path):
            return False
        if self._queue_play_after_stop_fade(
            file_path,
            space_triggered=space_triggered,
            preserve_range_latch=preserve_range_latch,
            start_ms=start_ms,
            force_restart=force_restart,
            overlap=overlap,
        ):
            return True

        self._pending_space_restart = None
        abs_path = os.path.abspath(file_path)
        if not preserve_range_latch:
            self._range_end_latched = False
        if self._displayed_track_path and self._displayed_track_path != abs_path:
            self._save_timeline_for_path(self._displayed_track_path)
        if self._current_file_path and os.path.abspath(self._current_file_path) != abs_path:
            self._save_timeline_for_path(self._current_file_path)

        current_abs = os.path.abspath(self._current_file_path) if self._current_file_path else None
        air_playing = (
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if (
            not force_restart
            and current_abs == abs_path
            and air_playing
        ):
            return True
        switching_track = (
            air_playing
            and current_abs is not None
            and (current_abs != abs_path or force_restart)
        )

        fade_ms = self._transition_fade_ms(space_triggered, overlap=overlap)
        self._pending_transition_fade_ms = fade_ms if switching_track else 0
        # Block EOF/range-end of the dying track from cancelling this takeover
        # (common when Space is pressed in the last ~0.5s).
        if switching_track:
            self._suppress_range_end = True
        self._cancel_crossfade()
        if switching_track:
            # _cancel_crossfade clears suppress; restore for this takeover.
            self._suppress_range_end = True
            self._pending_transition_fade_ms = fade_ms

        def begin_playback():
            self.volume_fader.cancel()
            self._cancel_pending_playback()
            other_idx = 1 - self._active_player_idx
            self._players[other_idx].stop()
            self._apply_player_volume(other_idx, 0.0)
            self._current_file_path = file_path
            self._fade_out_started = False
            self._playback_token += 1
            self._pending_playback_token = self._playback_token
            self._pending_playback_start = True
            self._pending_playback_player_idx = self._active_player_idx
            is_new_track = self._displayed_track_path != abs_path
            needs_waveform = is_new_track or not self.waveform.waveform_data
            self.waveform.reset_view()
            if is_new_track:
                self.waveform.set_waveform([])
            elif needs_waveform:
                self.waveform.set_waveform([])
            self._displayed_track_path = abs_path
            self._prepare_timeline_for_path(file_path)
            self._update_file_properties(file_path)
            self._pending_start_ms = (
                start_ms if start_ms is not None else self._range_start_ms()
            )
            self.waveform.set_position(self._pending_start_ms)

            active = self._players[self._active_player_idx]
            current_src = active.source().toLocalFile()
            same_source = bool(
                current_src and os.path.abspath(current_src) == abs_path
            )
            # QMediaPlayer often ignores setSource(same URL); after Stop that
            # leaves _pending_playback_start stuck and silence until another Space.
            if force_restart and same_source and self._media_ready_for_seek(
                self._active_player_idx
            ):
                play_at = self._pending_start_ms
                self._pending_playback_start = False
                self._pending_start_ms = None
                self._start_playback_at(play_at, player_idx=self._active_player_idx)
                self.update_track_info_playing()
                if needs_waveform:
                    QTimer.singleShot(0, lambda path=file_path: self._request_waveform(path))
                self._request_bpm_detection(file_path)
                self.update_time_display()
                self._update_timeline_labels()
                return

            if active.source().isValid():
                active.stop()
            if same_source:
                self._set_air_source(self._active_player_idx, None)
            self._set_air_source(self._active_player_idx, file_path)
            self.update_track_info_playing()
            if needs_waveform:
                QTimer.singleShot(0, lambda path=file_path: self._request_waveform(path))
            self._request_bpm_detection(file_path)
            self.update_time_display()
            self._update_timeline_labels()

        if fade_ms > 0 and switching_track and not preserve_range_latch:
            if self._is_crossfade_mode() or overlap:
                self._start_crossfade_transition(
                    file_path,
                    abs_path,
                    fade_ms,
                    start_ms=start_ms,
                )
            else:
                self._apply_fade_out(begin_playback, duration_ms=fade_ms)
        else:
            if switching_track or preserve_range_latch:
                if not self._crossfade_active:
                    self.volume_fader.cancel()
                    self._set_output_volume(self._playback_volume)
            begin_playback()
        return True

    def _step_selection_forward(self) -> bool:
        visible = self.get_visible_playlists()
        if not visible:
            return False

        if self.current_playing_playlist is None or self.current_playing_item is None:
            for playlist in visible:
                if playlist.count() > 0:
                    playlist.setCurrentRow(0)
                    self.current_playing_playlist = playlist.playlist_num
                    self.current_playing_item = playlist.item(0)
                    return True
            return False

        current_playlist = None
        current_visible_idx = -1
        for idx, playlist in enumerate(visible):
            if playlist.playlist_num == self.current_playing_playlist:
                current_playlist = playlist
                current_visible_idx = idx
                break
        if current_playlist is None:
            current_playlist = visible[0]
            current_visible_idx = 0

        # Advance from the playing item, not from the UI selection cursor.
        current_row = -1
        for row in range(current_playlist.count()):
            if current_playlist.item(row) is self.current_playing_item:
                current_row = row
                break
        if current_row < 0:
            current_row = max(0, current_playlist.currentRow())

        if current_row < current_playlist.count() - 1:
            new_row = current_row + 1
            current_playlist.setCurrentRow(new_row)
            self.current_playing_playlist = current_playlist.playlist_num
            self.current_playing_item = current_playlist.item(new_row)
            return True

        for idx in range(current_visible_idx + 1, len(visible)):
            next_playlist = visible[idx]
            if next_playlist.count() <= 0:
                continue
            next_playlist.setCurrentRow(0)
            self.current_playing_playlist = next_playlist.playlist_num
            self.current_playing_item = next_playlist.item(0)
            return True

        if self.playback_mode == "next" and current_playlist.count() > 0:
            current_playlist.setCurrentRow(0)
            self.current_playing_playlist = current_playlist.playlist_num
            self.current_playing_item = current_playlist.item(0)
            return True
        return False

    def _auto_advance_to_next_track(self) -> None:
        if not self._play_next_playable_track():
            self._range_end_latched = False
            self.stop()

    def _play_next_playable_track(self) -> bool:
        visible = self.get_visible_playlists()
        total_tracks = sum(playlist.count() for playlist in visible)
        if total_tracks == 0:
            return False

        for _ in range(total_tracks):
            if not self._step_selection_forward():
                return False
            if self.current_playing_playlist is None or self.current_playing_item is None:
                continue
            path = get_item_file_path(self.current_playing_item)
            if not path or not os.path.isfile(path):
                continue
            # Fresh start: do not keep end-latch; force reload after EOF/pause.
            self._range_end_latched = False
            if self.play_file(path, force_restart=True, overlap=True):
                return True
        return False

    def advance_selection(self):
        visible = self.get_visible_playlists()
        if not visible:
            return

        playlist_num, _ = self.get_selected_track()
        if playlist_num is None:
            if visible[0].count() > 0:
                visible[0].setCurrentRow(0)
                self.last_selected_playlist = 1
            return

        current_idx = self._playlist_index(playlist_num)
        if current_idx >= len(visible):
            current_idx = 0

        current_playlist = visible[current_idx]
        current_num = current_idx + 1
        current_row = current_playlist.currentRow()

        if current_row < current_playlist.count() - 1:
            current_playlist.setCurrentRow(current_row + 1)
            self.last_selected_playlist = current_num
        elif current_idx < len(visible) - 1 and visible[current_idx + 1].count() > 0:
            visible[current_idx + 1].setCurrentRow(0)
            self.last_selected_playlist = current_idx + 2
        elif current_playlist.count() > 0:
            current_playlist.setCurrentRow(0)
            self.last_selected_playlist = current_num

    def update_ready_for_selection(self):
        playlist_num, item = self.get_selected_track()
        if item:
            file_path = get_item_file_path(item)
            name = self.get_track_display_name(item, file_path)
            self.track_info.setText(name)
        else:
            self.track_info.setText("No track selected")

    def _arm_space_restart_from_air(self) -> None:
        """Remember the air track so the next Space can restart it after Stop/Escape."""
        path = self._current_file_path
        item = self.current_playing_item
        playlist_num = self.current_playing_playlist
        if (
            not path
            or not item
            or playlist_num is None
            or not os.path.isfile(path)
        ):
            return
        if not self._is_playback_active() and not self.player.source().isValid():
            return
        self._pending_space_restart = {
            "path": path,
            "item": item,
            "playlist_num": playlist_num,
        }

    def _restart_pending_air_track(self) -> bool:
        target = self._pending_space_restart
        self._pending_space_restart = None
        if not target:
            return False
        path = target.get("path")
        item = target.get("item")
        playlist_num = target.get("playlist_num")
        if not path or not item or playlist_num is None or not os.path.isfile(path):
            return False

        # Abort an in-progress stop fade so Space can restart the same track.
        self._stop_fade_active = False
        self._queued_after_stop_fade = None
        self._stop_fade_path = None
        self._stop_fade_item = None
        self._stop_fade_playlist_num = None
        if self._crossfade_active:
            self._cancel_crossfade()
        self.volume_fader.cancel()
        self._range_end_latched = False
        self._suppress_range_end = False
        self._pending_transition_fade_ms = 0
        self._fade_out_started = False
        self.player.stop()
        self._set_output_volume(self._playback_volume)

        self.current_playing_playlist = playlist_num
        self.current_playing_item = item
        return self.play_file(path, force_restart=True)

    def _preview_differs_from_track(self, path: str | None, item=None) -> bool:
        """True if preview/selection is a different playable track than path/item."""
        if self._preview_file_path and self._preview_item and os.path.isfile(
            self._preview_file_path
        ):
            preview_path = os.path.abspath(self._preview_file_path)
            if path and preview_path != os.path.abspath(path):
                return True
            if item is not None and self._preview_item is not item:
                return True
            return False

        _playlist_num, selected = self.get_selected_track()
        if selected is None:
            return False
        selected_path = get_item_file_path(selected)
        if not selected_path or not os.path.isfile(selected_path):
            return False
        if path and os.path.abspath(selected_path) != os.path.abspath(path):
            return True
        if item is not None and selected is not item:
            return True
        return False

    def _should_consume_space_restart(self) -> bool:
        """Restart the stopped air track only if that same track is still selected."""
        pending = self._pending_space_restart
        if not pending or not pending.get("path"):
            return False
        # Another selected/preview track must go to air, even if it was
        # already chosen before Escape/Stop (e.g. advance-on-space).
        if self._preview_differs_from_track(pending.get("path"), pending.get("item")):
            return False
        return True

    def handle_space(self):
        if self._should_consume_space_restart():
            if self._restart_pending_air_track():
                return
        else:
            self._pending_space_restart = None

        playlist_num, item = self.get_selected_track()
        preview_ready = (
            self._preview_file_path
            and self._preview_item
            and os.path.isfile(self._preview_file_path)
        )

        if preview_ready:
            self.take_preview_to_air(space_triggered=True)
            if self.space_advances_checkbox.isChecked():
                self.advance_selection()
                playlist_num, item = self.get_selected_track()
                if item:
                    self.load_preview_track(playlist_num, item)
            return

        is_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        is_same_track = (
            item is not None
            and self.current_playing_item is item
            and self.current_playing_playlist == playlist_num
        )

        if is_playing and is_same_track:
            return

        if self.space_advances_checkbox.isChecked():
            if playlist_num is None:
                visible = self.get_visible_playlists()
                if visible and visible[0].count() > 0:
                    visible[0].setCurrentRow(0)
                    self.last_selected_playlist = 1
                    playlist_num = 1
                    item = visible[0].currentItem()
                else:
                    return
            if item:
                self.load_preview_track(playlist_num, item)
            self.take_preview_to_air(space_triggered=True)
            self.advance_selection()
            return

        if playlist_num and item and not is_same_track:
            self.load_preview_track(playlist_num, item)
            self.take_preview_to_air(space_triggered=True)
            return

        if self.player.source().isValid():
            self._resume_with_fade()
        elif playlist_num and item:
            self.load_preview_track(playlist_num, item)
            self.take_preview_to_air(space_triggered=True)
        else:
            for i, playlist in enumerate(self.get_visible_playlists(), start=1):
                if playlist.count() > 0:
                    playlist.setCurrentRow(0)
                    item = playlist.currentItem()
                    self.load_preview_track(i, item)
                    self.take_preview_to_air(space_triggered=True)
                    return

    def toggle_play(self):
        if self._crossfade_active:
            self._cancel_crossfade()
        if self._stop_fade_active:
            if self._preview_differs_from_track(
                self._stop_fade_path or self._current_file_path,
                self._stop_fade_item or self.current_playing_item,
            ):
                self._pending_space_restart = None
                if self._preview_file_path and self._preview_item:
                    self.take_preview_to_air()
                    return
                playlist_num, item = self.get_selected_track()
                if playlist_num and item:
                    self.load_preview_track(playlist_num, item)
                    self.take_preview_to_air()
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.volume_fader.cancel()
            self._set_output_volume(self._playback_volume)
            self.player.pause()
            self.update_track_info_ready()
            return
        if (
            self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
            and self._preview_differs_from_track(
                self._current_file_path,
                self.current_playing_item,
            )
        ):
            self._pending_space_restart = None
            if self._preview_file_path and self._preview_item:
                self.take_preview_to_air()
                return
            playlist_num, item = self.get_selected_track()
            if playlist_num and item:
                self.load_preview_track(playlist_num, item)
                self.take_preview_to_air()
                return
        if self.player.source().isValid():
            self._resume_with_fade()

    def pause(self):
        if self._crossfade_active:
            self._cancel_crossfade()
        if self._stop_fade_active:
            self._stop_fade_active = False
            self._queued_after_stop_fade = None
            self._stop_fade_path = None
            self._stop_fade_item = None
            self._stop_fade_playlist_num = None
        self.volume_fader.cancel()
        self._set_output_volume(self._playback_volume)
        self.player.pause()
        self.update_track_info_ready()

    def stop(self):
        if self._stop_fade_active:
            # Second Stop cancels a queued next track but keeps the fade-out.
            self._clear_queued_after_stop_fade(restore_air_identity=True)
            return

        self._arm_space_restart_from_air()
        if self._crossfade_active:
            self._cancel_crossfade()
        fade_ms = self._space_fade_duration_ms()
        is_active = self._is_playback_active()

        def finish_stop():
            queued = self._queued_after_stop_fade
            self._stop_fade_active = False
            self._stop_fade_path = None
            self._stop_fade_item = None
            self._stop_fade_playlist_num = None
            self._queued_after_stop_fade = None
            self.volume_fader.cancel()
            self._range_end_latched = False
            self._suppress_range_end = False
            self._pending_transition_fade_ms = 0
            self.player.stop()
            self._set_output_volume(self._playback_volume)
            self._fade_out_started = False
            self._clear_air_spectrum()
            if queued:
                self._start_queued_after_stop_fade(queued)
                return
            self.update_track_info_ready()
            self.update_time_display()

        if fade_ms > 0 and is_active:
            self._stop_fade_active = True
            self._stop_fade_path = (
                os.path.abspath(self._current_file_path)
                if self._current_file_path
                else None
            )
            self._stop_fade_item = self.current_playing_item
            self._stop_fade_playlist_num = self.current_playing_playlist
            self._suppress_range_end = True
            self._apply_fade_out(finish_stop, use_spinbox=True)
        else:
            finish_stop()

    def _queue_play_after_stop_fade(
        self,
        file_path: str,
        *,
        space_triggered: bool = False,
        preserve_range_latch: bool = False,
        start_ms: int | None = None,
        force_restart: bool = False,
        overlap: bool = False,
    ) -> bool:
        """Hold a next-track start until Stop fade-out finishes."""
        if not self._stop_fade_active:
            return False
        if not self.volume_fader.is_active:
            self._stop_fade_active = False
            return False
        fading = self._stop_fade_path
        if fading and os.path.abspath(file_path) == fading:
            return False
        self._queued_after_stop_fade = {
            "file_path": file_path,
            "space_triggered": space_triggered,
            "preserve_range_latch": preserve_range_latch,
            "start_ms": start_ms,
            "force_restart": force_restart,
            "overlap": overlap,
            "playlist_num": self.current_playing_playlist,
            "item": self.current_playing_item,
        }
        return True

    def _clear_queued_after_stop_fade(self, *, restore_air_identity: bool = False) -> None:
        queued = self._queued_after_stop_fade
        self._queued_after_stop_fade = None
        if not restore_air_identity or not queued:
            return
        if self._stop_fade_playlist_num is not None:
            self.current_playing_playlist = self._stop_fade_playlist_num
        if self._stop_fade_item is not None:
            self.current_playing_item = self._stop_fade_item

    def _start_queued_after_stop_fade(self, queued: dict) -> None:
        playlist_num = queued.get("playlist_num")
        item = queued.get("item")
        if playlist_num is not None:
            self.current_playing_playlist = playlist_num
        if item is not None:
            self.current_playing_item = item
        self.play_file(
            queued["file_path"],
            space_triggered=bool(queued.get("space_triggered")),
            preserve_range_latch=bool(queued.get("preserve_range_latch")),
            start_ms=queued.get("start_ms"),
            force_restart=bool(queued.get("force_restart", True)),
            overlap=bool(queued.get("overlap")),
        )

    def _playlist_index(self, playlist_num: int) -> int:
        return playlist_num - 1

    def next_track(self):
        visible = self.get_visible_playlists()
        total_tracks = sum(playlist.count() for playlist in visible)
        for _ in range(max(1, total_tracks)):
            if not self._step_selection_forward():
                return
            path = get_item_file_path(self.current_playing_item)
            if not path or not os.path.isfile(path):
                continue
            if self.play_file(path, force_restart=True):
                return

    def previous_track(self):
        visible = self.get_visible_playlists()
        if not visible:
            return

        if self.current_playing_playlist is None:
            if visible[0].count() > 0:
                visible[0].setCurrentRow(0)
                self.play_selected(1)
            return

        current_idx = self._playlist_index(self.current_playing_playlist)
        if current_idx >= len(visible):
            current_idx = 0

        current_playlist = visible[current_idx]
        current_num = current_idx + 1
        current_row = current_playlist.currentRow()

        if current_row > 0:
            current_playlist.setCurrentRow(current_row - 1)
            self.play_selected(current_num)
        elif current_idx > 0 and visible[current_idx - 1].count() > 0:
            prev_playlist = visible[current_idx - 1]
            prev_playlist.setCurrentRow(prev_playlist.count() - 1)
            self.play_selected(current_idx)

    def seek(self, position):
        if self._crossfade_active:
            self._cancel_crossfade()
        self._range_end_latched = False
        duration = self.player.duration()
        if duration <= 0:
            return
        start_ms = self._range_start_ms()
        end_ms = self._range_end_ms()
        position = max(start_ms, min(position, end_ms))
        if position >= 0 and position <= duration:
            self.player.setPosition(position)
            self.waveform.set_position(position)
            self._fade_out_started = False
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._apply_timeline_volume(position)
            else:
                self.volume_fader.cancel()
                if self._timeline_fades_enabled() and (
                    self._fade_in_ms() > 0 or self._fade_out_ms() > 0
                ):
                    self._set_output_volume(self._volume_for_position(position))
                else:
                    self._set_output_volume(self._playback_volume)
            self.update_time_display()

    def _should_ignore_range_end(self) -> bool:
        """True while a track takeover / crossfade must not be interrupted by EOF."""
        return bool(
            self._suppress_range_end
            or self._stop_fade_active
            or self._crossfade_active
            or self._pending_playback_start
            or self._handling_range_end
            or self._range_end_latched
        )

    def _handle_range_end(self) -> None:
        if self._should_ignore_range_end():
            return
        self._handling_range_end = True
        try:
            self.volume_fader.cancel()

            if self.playback_mode == "loop":
                self._range_end_latched = False
                self._fade_out_started = False
                start_ms = self._range_start_ms()
                self.waveform.set_position(start_ms)
                self.player.setPosition(start_ms)
                self.player.play()
                self._apply_fade_in()
            elif self.playback_mode == "next":
                if self._current_file_path:
                    self._save_timeline_for_path(self._current_file_path)
                self._range_end_latched = True
                QTimer.singleShot(0, self._auto_advance_to_next_track)
            else:
                self._range_end_latched = True
                self.stop()
        finally:
            self._handling_range_end = False

    def _check_fade_out_and_range(self, position: int) -> None:
        if self._should_ignore_range_end():
            return

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._apply_timeline_volume(position)

        range_end = self._range_end_ms()
        if range_end <= 0:
            return
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return

        # NEXT: start the following track `fade` ms before the current one ends.
        if self.playback_mode == "next":
            fade_ms = self._space_fade_duration_ms()
            start_ms = self._range_start_ms()
            span = max(0, range_end - start_ms)
            lead = min(fade_ms, max(0, span - 50)) if fade_ms > 0 else 0
            if position >= range_end - lead:
                self._handle_range_end()
                return

        if position >= range_end:
            self._handle_range_end()

    def update_time_display(self, *_args):
        duration = self.player.duration()
        position = self.player.position()

        if duration <= 0:
            self.time_duration_label.setText("--:--")
            self.time_large_label.setText("--:--")
            self.timeline_start_label.setText("0:00")
            self.timeline_end_label.setText("0:00")
            return

        self.time_duration_label.setText(format_time_ms(duration))

        if self._time_display_mode == "remaining":
            current_ms = max(0, duration - position)
            self.time_large_label.setText(f"-{format_time_ms(current_ms)}")
        else:
            self.time_large_label.setText(format_time_ms(position))

        self.timeline_start_label.setText(format_time_ms(self._range_start_ms()))
        self.timeline_end_label.setText(format_time_ms(duration))

    def _update_timeline_labels(self) -> None:
        duration = self.player.duration()
        if duration > 0:
            self.timeline_end_label.setText(format_time_ms(duration))

    def position_changed(self, position):
        if (
            self._seek_target_ms is not None
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            if abs(position - self._seek_target_ms) > 50:
                self.player.setPosition(self._seek_target_ms)
            else:
                self._seek_target_ms = None

        if not self._is_window_resizing and not self.waveform.is_scrubbing:
            self.waveform.set_position(position)
        self._check_fade_out_and_range(position)
        self.update_time_display()
        self._sync_spectrum_playback()

    def duration_changed(self, duration):
        if duration <= 0 and self._pending_playback_start:
            return
        self.waveform.set_duration(duration)
        if self._current_file_path and duration > 0 and not self._pending_playback_start:
            self._restore_timeline_state(self._current_file_path, duration)
        if self.current_playing_item and duration > 0:
            set_item_duration(self.current_playing_item, duration)
            for playlist in self.playlists:
                playlist.viewport().update()
                self.refresh_playlist_footer(playlist)
        self.update_time_display()

    def update_position(self):
        if self._crossfade_active:
            self._outgoing_fader.advance()
            self._incoming_fader.advance()
            if (
                self._crossfade_incoming_ready_to_play
                and self._pending_playback_start
                and self._crossfade_incoming_idx is not None
                and self._players[self._crossfade_incoming_idx].playbackState()
                != QMediaPlayer.PlaybackState.PlayingState
            ):
                self._try_begin_pending_playback()
        else:
            self.volume_fader.advance()
        self._preview_volume_fader.advance()
        if self._preview_player.duration() > 0 and not self._is_window_resizing:
            preview_position = self._preview_player.position()
            if (
                self._preview_seek_target_ms is not None
                and self._preview_player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
            ):
                if abs(preview_position - self._preview_seek_target_ms) > 50:
                    self._preview_player.setPosition(self._preview_seek_target_ms)
                else:
                    self._preview_seek_target_ms = None
            if not self.preview_waveform.is_scrubbing:
                self.preview_waveform.set_position(preview_position)
            self._check_preview_fade_out_and_range(preview_position)
            self._update_preview_time_display()
        if self._is_incoming_crossfade_loading():
            return
        if self.player.duration() > 0 and not self._is_window_resizing:
            position = self.player.position()
            if (
                self._seek_target_ms is not None
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            ):
                if abs(position - self._seek_target_ms) > 50:
                    self.player.setPosition(self._seek_target_ms)
                else:
                    self._seek_target_ms = None
            if not self.waveform.is_scrubbing:
                self.waveform.set_position(position)
            self._check_fade_out_and_range(position)
            self.update_time_display()

    def _on_player_playback_state_changed(self, player_idx: int, state) -> None:
        if player_idx != self._active_player_idx:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._reapply_output_volume()
        self._sync_spectrum_playback()

    def _on_player_position_changed(self, player_idx: int, position: int) -> None:
        if self._is_incoming_crossfade_loading() and player_idx == self._crossfade_outgoing_idx:
            return
        if player_idx != self._active_player_idx:
            return
        self.position_changed(position)

    def _on_player_duration_changed(self, player_idx: int, duration: int) -> None:
        if player_idx != self._active_player_idx:
            pending_idx = self._pending_playback_player_idx
            if not (self._pending_playback_start and pending_idx == player_idx):
                return
        self.duration_changed(duration)

    def _on_player_media_status_changed(self, player_idx: int, status) -> None:
        if (
            self._crossfade_active
            and player_idx == self._crossfade_incoming_idx
            and self._pending_playback_start
            and status in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            )
        ):
            duration = self._players[player_idx].duration()
            if duration > 0:
                self._prepare_incoming_crossfade_media(duration)
            if self._crossfade_incoming_ready_to_play:
                self._try_begin_pending_playback()
            return

        pending_idx = self._pending_playback_player_idx
        if pending_idx is None:
            pending_idx = self._active_player_idx

        if (
            status in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            )
            and self._pending_playback_start
            and player_idx == pending_idx
        ):
            duration = self._players[player_idx].duration()
            if duration > 0:
                self._start_pending_playback(duration, player_idx)
                if player_idx == self._active_player_idx:
                    self._reapply_output_volume()
            else:
                self._try_begin_pending_playback()
            return

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._crossfade_active and player_idx == self._crossfade_outgoing_idx:
                return
            if player_idx != self._active_player_idx:
                return
            # Range-end handler already advanced / looped — avoid double NEXT skip.
            # Also ignore EOF while Space takeover fade/crossfade is in flight.
            if self._should_ignore_range_end():
                return
            if self.playback_mode == "next":
                if self._current_file_path:
                    self._save_timeline_for_path(self._current_file_path)
                self._range_end_latched = True
                QTimer.singleShot(0, self._auto_advance_to_next_track)
            elif self.playback_mode == "loop":
                self._fade_out_started = False
                start_ms = self._range_start_ms()
                self.waveform.set_position(start_ms)
                self.player.setPosition(start_ms)
                self.player.play()
                self._apply_fade_in()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Right:
            self.next_track()
        elif event.key() == Qt.Key.Key_Left:
            self.previous_track()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._save_eq_for_bound_path()
        self._cancel_crossfade()
        self._res_waveform_timer.stop()
        self.timer.stop()
        self._resize_finish_timer.stop()
        self.volume_fader.cancel()
        self._outgoing_fader.cancel()
        self._incoming_fader.cancel()
        self._preview_volume_fader.cancel()
        for media_player in self._players:
            media_player.stop()
        self._preview_player.stop()
        self._browser_player.stop()
        self._save_current_timeline_state()
        self.bpm_detector.shutdown()
        self.waveform_loader.shutdown()
        self.preview_waveform_loader.shutdown()
        self.browser_waveform_loader.shutdown()
        if self._spectrum_timer is not None:
            self._spectrum_timer.stop()
        self.save_project()
        self.save_config()
        event.accept()
        super().closeEvent(event)
