from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QSizePolicy, QSplitter

from app.project import import_track_into_project, is_under_project
from app.video_mixer.layer_audio import LayerAudioStore
from app.video_mixer.media_store import MediaStore
from app.video_mixer.model import MixerModel, Scene
from app.video_mixer.output_window import OutputWindow
from app.video_mixer.panel import VideoMixerPanel
from app.video_mixer.scene_render import render_scene_thumb
from app.video_mixer.decoder_pool import ensure_pool

if TYPE_CHECKING:
    from app.player import AudioPlayer
    from PyQt6.QtWidgets import QWidget


class VideoMixerController(QObject):
    """Owns model, media store, panel wiring, output window, and render clock."""

    def __init__(self, window: AudioPlayer):
        super().__init__(window)
        self.window = window
        self.model = MixerModel(self)
        # Warm decode workers before any Take so video starts without spawn lag.
        ensure_pool()
        self.media_store = MediaStore(self.model)
        self.layer_audio = LayerAudioStore(self.model)
        self.panel = VideoMixerPanel(self.model, self.media_store)
        self.output_window = OutputWindow(self.model, self.media_store)
        self._views_dirty = True
        self.main_splitter: QSplitter | None = None
        self._left_column: QWidget | None = None
        self._transitioning = False
        self._take_scene_id: str | None = None
        self._preload_deadline_ms = 0
        self._tick_ms = 33
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(180)
        self._thumb_timer.timeout.connect(self._capture_preview_thumb)
        self._applying_project = False

        self.expand_btn = QPushButton("»")
        self.expand_btn.setObjectName("mixerExpandBtn")
        self.expand_btn.setToolTip("Expand video mixer")
        self.expand_btn.setFixedWidth(18)
        self.expand_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.expand_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.expand_btn.hide()
        self.expand_btn.clicked.connect(self.expand_panel)

        self.panel.collapseRequested.connect(self.collapse_panel)
        self.panel.openOutputRequested.connect(self.show_output_menu)
        self.panel.fullscreenRequested.connect(self.open_fullscreen_output)
        self.panel.mainSceneRequested.connect(self.take_scene_to_main)
        self.panel.clearPreviewRequested.connect(self.clear_preview)
        self.panel.transition_ms.valueChanged.connect(self._on_transition_setting)
        self.panel.panel_splitter.splitterMoved.connect(self._on_panel_splitter_moved)
        self.panel.bottom_splitter.splitterMoved.connect(self._on_panel_splitter_moved)
        self.model.changed.connect(self._on_model_changed)
        self.model.layerTransformChanged.connect(self._on_transform_changed)
        self.model.playbackChanged.connect(self._on_playback_changed)

        self._clock = QTimer(self)
        self._clock.setInterval(self._tick_ms)
        self._clock.timeout.connect(self._on_tick)
        self._clock.start()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(900)
        self._save_timer.timeout.connect(self._save_to_project)

        self._screen_refresh_timer = QTimer(self)
        self._screen_refresh_timer.setInterval(3000)
        self._screen_refresh_timer.timeout.connect(self._maybe_refresh_screens)
        self._screen_refresh_timer.start()

        # Project state is applied later via apply_project_state() from AudioPlayer.
        project = getattr(window, "project", None)
        self.bind_project_root(getattr(project, "root", None))
        self.panel._restore_splitters()
        self.media_store.sync()
        self.refresh_output_screens()
        self.panel.output_screen_combo.currentIndexChanged.connect(self._on_output_screen_chosen)
        self._schedule_thumb_capture()

        shortcut = QShortcut(QKeySequence("Ctrl+Shift+O"), window)
        shortcut.activated.connect(self.open_output_default)

    def bind_project_root(self, root: Path | str | None) -> None:
        self.model.project_root = Path(root) if root is not None else None

    def apply_project_state(self, data: dict | None) -> None:
        """Load mixer document from the active project (or reset to empty)."""
        self._applying_project = True
        try:
            self.bind_project_root(self.window.project.root)
            if isinstance(data, dict) and data:
                self.model.load_dict(data)
            else:
                empty = Scene.create("Scene 1")
                self.model.load_dict(
                    {
                        "scenes": [empty.to_dict()],
                        "main_scene_id": empty.id,
                        "preview_scene_id": empty.id,
                        "selected_layer_id": None,
                    }
                )
            self.panel.reload_thumbs()
            self.panel._restore_splitters()
            if self.main_splitter is not None:
                self._restore_splitters_deferred()
            self.media_store.sync()
            self.layer_audio.sync()
            self._apply_collapsed_state(self.model.panel_collapsed)
            self._views_dirty = True
            self.panel.refresh_all()
        finally:
            self._applying_project = False

    def import_media_into_project(self) -> bool:
        """Copy external layer media into project/media/. Returns True if paths changed."""
        root = self.window.project.root
        changed = False
        for scene in self.model.scenes:
            for layer in scene.layers:
                path = (layer.path or "").strip()
                if not path or not Path(path).is_file():
                    continue
                if is_under_project(path, root):
                    continue
                new_path = import_track_into_project(path, root, move=False)
                if new_path and new_path != path:
                    layer.path = new_path
                    changed = True
        if changed:
            self.media_store.sync()
            self.layer_audio.sync()
            self._views_dirty = True
        return changed

    def export_state(self) -> dict:
        if self.main_splitter is not None and not self.model.panel_collapsed:
            self.model.splitter_main = self.main_splitter.sizes()
        self.panel._persist_splitters()
        if self.panel.width() > 0:
            self.model.panel_width = max(280, self.panel.width())
        return self.model.to_dict()

    def _save_to_project(self) -> None:
        if self._applying_project:
            return
        if getattr(self.window, "_saving_project", False):
            return
        # Lightweight mixer-only save — full save_project stalls UI / clicks air.
        if hasattr(self.window, "save_video_mixer_only"):
            self.window.save_video_mixer_only()
        elif hasattr(self.window, "save_project"):
            self.window.save_project()

    def attach_to_layout(self, parent_layout, left_column: QWidget, *, stretch: int = 1) -> None:
        self._left_column = left_column
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("mixerMainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(left_column)
        self.main_splitter.addWidget(self.panel)
        self.main_splitter.setStretchFactor(0, 7)
        self.main_splitter.setStretchFactor(1, 3)
        self.panel.setMinimumWidth(280)
        if self.model.splitter_main and len(self.model.splitter_main) == 2:
            self.main_splitter.setSizes(self.model.splitter_main)
        else:
            total = max(1200, self.window.width() or 1200)
            mixer_w = max(280, min(self.model.panel_width, total // 2))
            self.main_splitter.setSizes([total - mixer_w, mixer_w])

        self.main_splitter.splitterMoved.connect(self._on_main_splitter_moved)
        parent_layout.addWidget(self.main_splitter, stretch)
        parent_layout.addWidget(self.expand_btn, 0)
        self._apply_collapsed_state(self.model.panel_collapsed)
        # After the widget is in a real layout, re-apply sizes (setSizes before
        # show often gets overwritten by the first layout pass).
        self.panel._restore_splitters()
        QTimer.singleShot(0, self._restore_splitters_deferred)

    def _restore_splitters_deferred(self) -> None:
        self.panel._restore_splitters()
        if (
            self.main_splitter is not None
            and self.model.splitter_main
            and len(self.model.splitter_main) == 2
            and not self.model.panel_collapsed
        ):
            self.main_splitter.setSizes(self.model.splitter_main)

    def collapse_panel(self) -> None:
        self.model.panel_collapsed = True
        self._apply_collapsed_state(True)
        self._schedule_save()

    def expand_panel(self) -> None:
        self.model.panel_collapsed = False
        self._apply_collapsed_state(False)
        # setSizes after show often needs a layout pass
        QTimer.singleShot(0, self._restore_panel_width)
        self._schedule_save()

    def clear_preview(self) -> None:
        self.model.clear_preview()

    def _on_transition_setting(self, value: float = 0.0) -> None:
        self.model.set_transition_ms(int(value))
        self._schedule_save()

    def take_scene_to_main(self, scene_id: str) -> None:
        """Preload next scene while Main keeps playing, then crossfade."""
        if not any(s.id == scene_id for s in self.model.scenes):
            return
        if scene_id == self.model.main_scene_id and not self._transitioning:
            return
        if self._transitioning:
            self._take_scene_id = scene_id
            self.model.pending_main_scene_id = scene_id
            self.media_store.sync()
            return

        self._transitioning = True
        self._take_scene_id = scene_id
        # Keep live Main playing — do NOT freeze/hold during preload.
        self.model.pending_main_scene_id = scene_id
        self.media_store.sync()
        self._preload_deadline_ms = max(0, int(self.model.preload_ms))
        self._views_dirty = True

    def _finish_take_to_main(self) -> None:
        scene_id = self._take_scene_id
        if scene_id is None:
            self._transitioning = False
            self.model.pending_main_scene_id = None
            return

        scene = self.model.scene_by_id(scene_id)
        if scene is not None:
            for layer in scene.layers:
                if layer.kind != "video":
                    continue
                if layer.main_seek == "start":
                    layer.position_ms = 0

        # Snapshot live Main, then switch and crossfade.
        outgoing = self.panel.main_gl.capture_current()
        self.model.pending_main_scene_id = None
        self.model.set_main_scene(scene_id)
        self.media_store.sync()
        if scene is not None:
            for layer in scene.layers:
                if layer.kind != "video":
                    continue
                if layer.main_seek == "start":
                    self.media_store.seek_layer(layer.id, 0)
                else:
                    self.media_store.seek_layer(layer.id, layer.position_ms)
        self.media_store.tick(self._tick_ms)
        self.panel.main_gl.start_crossfade(self.model.transition_ms, outgoing)
        self._take_scene_id = None
        self._transitioning = False
        self._views_dirty = True

    def refresh_output_screens(self) -> None:
        combo = self.panel.output_screen_combo
        app = QApplication.instance()
        if app is None:
            return
        screens = app.screens()
        current_data = combo.currentData()
        preferred = self.model.output_screen_name
        combo.blockSignals(True)
        combo.clear()
        for index, screen in enumerate(screens):
            geo = screen.geometry()
            label = f"{screen.name()} ({geo.width()}×{geo.height()})"
            if screen is app.primaryScreen():
                label += " · primary"
            combo.addItem(label, index)
        # Restore selection by saved name or previous index
        select = 0
        if preferred:
            for i, screen in enumerate(screens):
                if screen.name() == preferred:
                    select = i
                    break
        elif isinstance(current_data, int) and 0 <= current_data < combo.count():
            select = current_data
        else:
            # Prefer non-primary
            primary = app.primaryScreen()
            for i, screen in enumerate(screens):
                if screen is not primary:
                    select = i
                    break
        if combo.count() > 0:
            combo.setCurrentIndex(select)
        combo.blockSignals(False)

    def _maybe_refresh_screens(self) -> None:
        combo = self.panel.output_screen_combo
        if combo.view().isVisible():
            return
        app = QApplication.instance()
        if app is None:
            return
        if combo.count() != len(app.screens()):
            self.refresh_output_screens()

    def _on_output_screen_chosen(self, _index: int = 0) -> None:
        app = QApplication.instance()
        if app is None:
            return
        idx = self.selected_output_screen_index()
        screens = app.screens()
        if idx is not None and 0 <= idx < len(screens):
            self.model.output_screen_name = screens[idx].name()
            self._schedule_save()

    def selected_output_screen_index(self) -> int | None:
        data = self.panel.output_screen_combo.currentData()
        return int(data) if isinstance(data, int) else None

    def open_fullscreen_output(self) -> None:
        self.refresh_output_screens()
        index = self.selected_output_screen_index()
        self.open_output_on(index)

    def open_output_default(self) -> None:
        self.open_fullscreen_output()

    def open_output_on(self, screen_index: int | None) -> None:
        try:
            self.output_window.open_on_screen(screen_index)
            self._views_dirty = True
            if not self._clock.isActive():
                self._clock.start()
        except Exception as exc:
            QMessageBox.warning(self.window, "Video Mixer", f"Cannot open output:\n{exc}")

    def show_output_menu(self) -> None:
        """Legacy: open fullscreen on selected combo screen."""
        self.open_fullscreen_output()

    def _capture_preview_thumb(self) -> None:
        """Freeze a scene thumbnail from Preview (or Main) once media is ready."""
        scene = self.model.preview_scene() or self.model.main_scene()
        if scene is None:
            return
        if not self.media_store.scene_media_ready(scene.id):
            # Retry shortly while decoders warm up
            self._thumb_timer.start(120)
            return
        self._capture_thumb(scene.id)

    def _capture_thumb(self, scene_id: str) -> None:
        scene = self.model.scene_by_id(scene_id)
        if scene is None:
            return
        pix = render_scene_thumb(self.model, self.media_store, scene, 128, 72)
        if pix is not None and not pix.isNull():
            self.panel.store_frozen_thumb(scene_id, pix)

    def _schedule_thumb_capture(self) -> None:
        self._thumb_timer.start()

    def _restore_panel_width(self) -> None:
        """Ensure the mixer side has a usable width after expand/show."""
        if self.main_splitter is None or self.model.panel_collapsed:
            return
        sizes = self.main_splitter.sizes()
        total = sum(sizes) if sizes else 0
        if total <= 0:
            total = max(800, self.main_splitter.width())
        mixer_w = max(280, int(self.model.panel_width or 360))
        if self.model.splitter_main and len(self.model.splitter_main) == 2:
            left, right = (int(self.model.splitter_main[0]), int(self.model.splitter_main[1]))
            if right >= 280 and left + right > 0:
                self.main_splitter.setSizes([left, right])
                return
        # Panel was hidden with ~0 width — rebuild from saved panel_width.
        if len(sizes) >= 2 and sizes[1] >= 280:
            return
        left = max(200, total - mixer_w)
        self.main_splitter.setSizes([left, mixer_w])

    def _apply_collapsed_state(self, collapsed: bool) -> None:
        if self.main_splitter is None:
            self.panel.setVisible(not collapsed)
            self.expand_btn.setVisible(collapsed)
            return
        if collapsed:
            # Capture width before hide — after hide splitter often reports 0.
            if self.panel.isVisible() and self.panel.width() > 0:
                self.model.panel_width = max(280, self.panel.width())
            sizes = self.main_splitter.sizes()
            if len(sizes) >= 2 and sizes[1] > 0:
                self.model.splitter_main = sizes
                self.model.panel_width = max(280, sizes[1])
        self.panel.setVisible(not collapsed)
        self.expand_btn.setVisible(collapsed)
        if collapsed:
            output_on = self.output_window.isVisible()
            if output_on:
                self.media_store.set_render_suspended(preview=True, all_media=False)
                if not self._clock.isActive():
                    self._clock.start()
            else:
                self.media_store.set_render_suspended(preview=False, all_media=True)
                self.layer_audio.release_all()
                self._clock.stop()
        else:
            self.media_store.set_render_suspended(preview=False, all_media=False)
            if not self._clock.isActive():
                self._clock.start()
            self._views_dirty = True
            self._restore_panel_width()

    def _on_main_splitter_moved(self, *_args) -> None:
        if self.main_splitter is None:
            return
        sizes = self.main_splitter.sizes()
        self.model.splitter_main = sizes
        if len(sizes) >= 2 and sizes[1] > 0:
            self.model.panel_width = sizes[1]
        self._schedule_save()

    def _on_panel_splitter_moved(self, *_args) -> None:
        self.panel._persist_splitters()
        self._schedule_save()

    def _on_model_changed(self) -> None:
        self.media_store.sync()
        self._views_dirty = True
        self._schedule_save()
        self._schedule_thumb_capture()

    def _on_transform_changed(self, _layer_id: str) -> None:
        self._views_dirty = True
        # Debounced light save only — no thumb re-render on every nudge.
        self._schedule_save()

    def _on_playback_changed(self, layer_id: str) -> None:
        layer = self.model.find_layer(layer_id)
        if layer is not None:
            media = self.media_store.get(layer_id)
            pos = int(layer.position_ms)
            # Switching into LOOP at EOF should wrap from the start.
            if (
                layer.playback == "loop"
                and media is not None
                and media.decoder is not None
                and media.decoder.at_eof
            ):
                pos = 0
                layer.position_ms = 0
            self.media_store.seek_layer(layer_id, pos)
        self._views_dirty = True

    def _schedule_save(self) -> None:
        if self._applying_project:
            return
        self._save_timer.start()

    def _sync_layer_audio(self) -> None:
        audible = self.layer_audio.sync()
        if not audible:
            return
        # Video bed is audible — pause air so two programs don't fight the device.
        # Only when output is truly open (not a zero-gate stub), to avoid air glitches.
        try:
            from PyQt6.QtMultimedia import QMediaPlayer

            if self.window.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            if not self.model.main_scene_has_audible_video():
                return
            self.window.pause()
        except Exception:
            pass

    def _on_tick(self) -> None:
        # Preload next scene while Main keeps playing live
        if self._transitioning and self._take_scene_id:
            frame_changed = self.media_store.tick(self._tick_ms)
            self._sync_layer_audio()
            ready = self.media_store.scene_media_ready(self._take_scene_id)
            self._preload_deadline_ms -= self._tick_ms
            need_draw = frame_changed or self._views_dirty
            panel_visible = self.panel.isVisible() and not self.model.panel_collapsed
            if panel_visible and need_draw:
                self.panel.refresh_views()
                self._views_dirty = False
            if self.output_window.isVisible() and need_draw:
                self.output_window.refresh()
            if ready and self._preload_deadline_ms <= 0:
                self._finish_take_to_main()
            elif self._preload_deadline_ms <= -2000:
                self._finish_take_to_main()
            return

        transitioning = self.panel.main_gl.tick_transition(self._tick_ms)
        frame_changed = self.media_store.tick(self._tick_ms)
        self._sync_layer_audio()
        need_draw = frame_changed or self._views_dirty or transitioning
        if not need_draw and not self.media_store.has_playing_video():
            return

        panel_visible = self.panel.isVisible() and not self.model.panel_collapsed
        if panel_visible and need_draw:
            self.panel.refresh_views()
            self._views_dirty = False
            layer = self.model.selected_layer()
            if (
                layer is not None
                and layer.kind == "video"
                and not self.panel.seek_slider.isSliderDown()
            ):
                self.panel.update_seek_readout()

        if self.output_window.isVisible() and need_draw:
            self.output_window.refresh()

    def shutdown(self) -> None:
        self._clock.stop()
        self._screen_refresh_timer.stop()
        self._save_timer.stop()
        if self.main_splitter is not None:
            self.model.splitter_main = self.main_splitter.sizes()
        self.panel._persist_splitters()
        self.model.panel_width = max(280, self.panel.width()) if self.panel.width() > 0 else self.model.panel_width
        try:
            self.panel.release_gl()
        except Exception:
            pass
        try:
            self.output_window.release()
        except Exception:
            pass
        self.layer_audio.release_all()
        self.media_store.release_all()
        self.output_window.hide()
