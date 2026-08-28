from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent, QPixmap, QResizeEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QInputDialog,
    QMessageBox,
)

from app.config import get_config_path
from app.project import scene_thumbs_dir
from app.ui.widgets.float_value_stepper import FloatValueStepper
from app.ui.widgets.section_header import SectionHeader
from app.ui.widgets.segment_button import SegmentButtonGroup
from app.video_mixer.gl_widget import MainGLWidget, PreviewGLWidget
from app.video_mixer.layer_list import LayerListWidget
from app.video_mixer.media_store import MediaStore
from app.video_mixer.media_utils import is_media_file
from app.video_mixer.model import MixerModel, MAX_SCENES


def _format_ms(ms: int) -> str:
    total = max(0, int(ms) // 1000)
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def _thumbs_dir(model: MixerModel) -> Path:
    if model.project_root is not None:
        return scene_thumbs_dir(model.project_root)
    path = get_config_path("scene_thumbs")
    path.mkdir(parents=True, exist_ok=True)
    return path


class SceneSlot(QFrame):
    mainRequested = pyqtSignal(str)  # left-click → Main Screen
    previewRequested = pyqtSignal(str)  # right-click → Preview
    addRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene_id: str | None = None
        self.is_add_slot = False
        self.setObjectName("sceneSlot")
        self.setFixedSize(68, 52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            "ЛКМ — Main Screen\n"
            "ПКМ — Preview"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)

        self.thumb = QLabel()
        self.thumb.setObjectName("sceneThumb")
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setFixedHeight(32)
        layout.addWidget(self.thumb, 1)

        self.name_label = QLabel("")
        self.name_label.setObjectName("sceneName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setWordWrap(False)
        layout.addWidget(self.name_label)

    def set_empty(self) -> None:
        self.scene_id = None
        self.is_add_slot = False
        self.setProperty("active", False)
        self.setProperty("preview", False)
        self.setProperty("addSlot", False)
        self.thumb.clear()
        self.name_label.clear()
        self._repolish()

    def set_add(self) -> None:
        self.scene_id = None
        self.is_add_slot = True
        self.setProperty("active", False)
        self.setProperty("preview", False)
        self.setProperty("addSlot", True)
        self.thumb.setText("+")
        self.name_label.setText("")
        self._repolish()

    def set_scene(
        self,
        scene_id: str,
        name: str,
        *,
        is_main: bool,
        is_preview: bool,
        pixmap: QPixmap | None = None,
    ) -> None:
        self.scene_id = scene_id
        self.is_add_slot = False
        self.setProperty("active", is_main)
        self.setProperty("preview", is_preview and not is_main)
        self.setProperty("addSlot", False)
        self.name_label.setText(name)
        if pixmap is not None and not pixmap.isNull():
            self.thumb.setPixmap(
                pixmap.scaled(
                    64,
                    30,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.thumb.setText("")
        else:
            self.thumb.clear()
            self.thumb.setText("")
        self._repolish()

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.is_add_slot and event.button() == Qt.MouseButton.LeftButton:
            self.addRequested.emit()
            event.accept()
            return
        if self.scene_id:
            if event.button() == Qt.MouseButton.LeftButton:
                self.mainRequested.emit(self.scene_id)
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                self.previewRequested.emit(self.scene_id)
                event.accept()
                return
        super().mousePressEvent(event)

class ScenesHost(QFrame):
    """Scenes wrap that notifies panel when resized (adaptive columns)."""

    resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("scenesWrap")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class VideoMixerPanel(QWidget):
    """Right-side video mixer chrome: screens, scenes, layers, properties."""

    collapseRequested = pyqtSignal()
    openOutputRequested = pyqtSignal()
    addLayerRequested = pyqtSignal()
    mainSceneRequested = pyqtSignal(str)
    clearPreviewRequested = pyqtSignal()
    fullscreenRequested = pyqtSignal()

    def __init__(self, model: MixerModel, media_store: MediaStore, parent=None):
        super().__init__(parent)
        self.model = model
        self.media_store = media_store
        self.setObjectName("videoMixerPanel")
        self.setAcceptDrops(True)
        self._updating_props = False
        self._frozen_thumbs: dict[str, QPixmap] = {}
        self._scenes_cols = 0
        self._scene_slots_by_id: dict[str, SceneSlot] = {}
        self._add_slot: SceneSlot | None = None
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(40)
        self._relayout_timer.timeout.connect(self._relayout_scenes)
        self._load_frozen_thumbs()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setSpacing(4)
        header_row.addWidget(SectionHeader("VIDEO MIXER"), 1)
        self.btn_collapse = QToolButton()
        self.btn_collapse.setObjectName("mixerToolBtn")
        self.btn_collapse.setText("»")
        self.btn_collapse.setToolTip("Collapse video mixer")
        self.btn_collapse.clicked.connect(self.collapseRequested.emit)
        header_row.addWidget(self.btn_collapse)
        root.addLayout(header_row)

        # Output: screen dropdown + fullscreen
        out_bar = QFrame()
        out_bar.setObjectName("screenResBar")
        out_layout = QHBoxLayout(out_bar)
        out_layout.setContentsMargins(4, 4, 4, 4)
        out_layout.setSpacing(6)
        out_title = QLabel("OUT")
        out_title.setObjectName("propTitle")
        out_layout.addWidget(out_title)
        self.output_screen_combo = QComboBox()
        self.output_screen_combo.setObjectName("mixerScreenCombo")
        self.output_screen_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.output_screen_combo.setToolTip("Monitor for fullscreen output")
        out_layout.addWidget(self.output_screen_combo, 1)
        self.btn_fullscreen = QToolButton()
        self.btn_fullscreen.setObjectName("mixerFullscreenBtn")
        self.btn_fullscreen.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_fullscreen.setText("Fullscreen")
        self.btn_fullscreen.setToolTip("Open fullscreen output on selected screen")
        self.btn_fullscreen.setMinimumWidth(88)
        self.btn_fullscreen.clicked.connect(self.fullscreenRequested.emit)
        out_layout.addWidget(self.btn_fullscreen)
        # Keep legacy name for controller menu fallback
        self.btn_output = self.btn_fullscreen
        root.addWidget(out_bar)

        # SCREEN resolution + transition duration
        screen_bar = QFrame()
        screen_bar.setObjectName("screenResBar")
        screen_layout = QHBoxLayout(screen_bar)
        screen_layout.setContentsMargins(4, 4, 4, 4)
        screen_layout.setSpacing(6)
        screen_title = QLabel("SCREEN")
        screen_title.setObjectName("propTitle")
        screen_layout.addWidget(screen_title)
        self.canvas_w = FloatValueStepper(
            self, 16, 16384, float(self.model.canvas_width), step=1, decimals=0, value_width=56
        )
        self.canvas_h = FloatValueStepper(
            self, 16, 16384, float(self.model.canvas_height), step=1, decimals=0, value_width=56
        )
        screen_layout.addWidget(self.canvas_w)
        mul = QLabel("×")
        mul.setObjectName("propAxis")
        screen_layout.addWidget(mul)
        screen_layout.addWidget(self.canvas_h)
        fade_label = QLabel("FADE")
        fade_label.setObjectName("propTitle")
        fade_label.setToolTip("Scene transition duration (ms)")
        screen_layout.addWidget(fade_label)
        self.transition_ms = FloatValueStepper(
            self, 0, 10000, float(self.model.transition_ms), step=50, decimals=0, value_width=56
        )
        self.transition_ms.setToolTip("Crossfade duration in milliseconds")
        screen_layout.addWidget(self.transition_ms)
        ms_label = QLabel("ms")
        ms_label.setObjectName("propAxis")
        screen_layout.addWidget(ms_label)
        screen_layout.addStretch(1)
        root.addWidget(screen_bar)

        # MAIN + PREVIEW in resizable top section
        screens = QWidget()
        screens_layout = QVBoxLayout(screens)
        screens_layout.setContentsMargins(0, 0, 0, 0)
        screens_layout.setSpacing(6)
        main_cap = QHBoxLayout()
        main_cap.setSpacing(4)
        main_cap.addWidget(self._screen_label("MAIN SCREEN"), 1)
        self.program_clock = QLabel("00:00:00")
        self.program_clock.setObjectName("programClock")
        self.program_clock.setToolTip("Timecode since this scene went to Main")
        self.program_clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        main_cap.addWidget(self.program_clock)
        screens_layout.addLayout(main_cap)
        self.main_gl = MainGLWidget(model, media_store, self)
        self.main_gl.setObjectName("mainScreen")
        self.main_gl.setMinimumHeight(80)
        self.main_gl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        screens_layout.addWidget(self.main_gl, 1)

        preview_cap = QHBoxLayout()
        preview_cap.setSpacing(4)
        preview_cap.addWidget(self._screen_label("PREVIEW SCREEN"), 1)
        self.btn_clear_preview = QToolButton()
        self.btn_clear_preview.setObjectName("mixerToolBtn")
        self.btn_clear_preview.setText("CLR")
        self.btn_clear_preview.setToolTip("Unload Preview (free decoders)")
        self.btn_clear_preview.clicked.connect(self.clearPreviewRequested.emit)
        preview_cap.addWidget(self.btn_clear_preview)
        screens_layout.addLayout(preview_cap)

        self.preview_gl = PreviewGLWidget(model, media_store, self)
        self.preview_gl.setObjectName("previewScreen")
        self.preview_gl.setMinimumHeight(80)
        self.preview_gl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        screens_layout.addWidget(self.preview_gl, 1)

        # SCENES (scrollable, adaptive columns)
        scenes_section = QWidget()
        scenes_section_layout = QVBoxLayout(scenes_section)
        scenes_section_layout.setContentsMargins(0, 0, 0, 0)
        scenes_section_layout.setSpacing(4)
        scenes_header = QHBoxLayout()
        scenes_header.setSpacing(4)
        scenes_header.addWidget(SectionHeader("SCENES"), 1)
        self.btn_rename_scene = QToolButton()
        self.btn_rename_scene.setObjectName("mixerToolBtn")
        self.btn_rename_scene.setText("Aa")
        self.btn_rename_scene.setToolTip("Rename preview scene")
        self.btn_rename_scene.clicked.connect(self._rename_preview_scene)
        scenes_header.addWidget(self.btn_rename_scene)
        self.btn_duplicate_scene = QToolButton()
        self.btn_duplicate_scene.setObjectName("mixerToolBtn")
        self.btn_duplicate_scene.setText("2×")
        self.btn_duplicate_scene.setToolTip("Duplicate preview scene")
        self.btn_duplicate_scene.clicked.connect(self._duplicate_preview_scene)
        scenes_header.addWidget(self.btn_duplicate_scene)
        self.btn_delete_scene = QToolButton()
        self.btn_delete_scene.setObjectName("mixerToolBtn")
        self.btn_delete_scene.setText("−")
        self.btn_delete_scene.setToolTip("Delete preview scene")
        self.btn_delete_scene.clicked.connect(self._delete_preview_scene)
        scenes_header.addWidget(self.btn_delete_scene)
        scenes_section_layout.addLayout(scenes_header)

        self.scenes_wrap = ScenesHost()
        self.scenes_grid = QGridLayout(self.scenes_wrap)
        self.scenes_grid.setContentsMargins(4, 4, 4, 4)
        self.scenes_grid.setHorizontalSpacing(4)
        self.scenes_grid.setVerticalSpacing(4)
        self.scenes_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scene_slots: list[SceneSlot] = []
        self.scenes_scroll = QScrollArea()
        self.scenes_scroll.setObjectName("scenesScroll")
        self.scenes_scroll.setWidgetResizable(True)
        self.scenes_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Always-on vertical bar keeps viewport width stable (no column flicker).
        self.scenes_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scenes_scroll.setWidget(self.scenes_wrap)
        self.scenes_scroll.setMinimumHeight(70)
        self.scenes_wrap.resized.connect(self._on_scenes_resized)
        scenes_section_layout.addWidget(self.scenes_scroll, 1)

        # LAYERS + PROPERTIES (horizontal splitter)
        layers_panel = QWidget()
        layers_col = QVBoxLayout(layers_panel)
        layers_col.setContentsMargins(0, 0, 0, 0)
        layers_col.setSpacing(4)
        layers_header = QHBoxLayout()
        layers_header.addWidget(SectionHeader("LAYERS"), 1)
        self.btn_add_layer = QToolButton()
        self.btn_add_layer.setObjectName("mixerToolBtn")
        self.btn_add_layer.setText("+")
        self.btn_add_layer.setToolTip("Add layer")
        self.btn_add_layer.clicked.connect(self._browse_add_layer)
        layers_header.addWidget(self.btn_add_layer)
        layers_col.addLayout(layers_header)

        self.layer_list = LayerListWidget()
        self.layer_list.setObjectName("layerList")
        self.layer_list.setMinimumHeight(80)
        self.layer_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.layer_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layer_list.layerSelected.connect(self.model.select_layer)
        self.layer_list.visibilityToggled.connect(self.model.set_layer_visible)
        self.layer_list.muteToggled.connect(self.model.set_layer_muted)
        self.layer_list.deleteRequested.connect(self._delete_layer)
        self.layer_list.reordered.connect(self._on_layers_reordered)
        layers_col.addWidget(self.layer_list, 1)

        props_panel = QWidget()
        props_col = QVBoxLayout(props_panel)
        props_col.setContentsMargins(0, 0, 0, 0)
        props_col.setSpacing(4)
        props_col.addWidget(SectionHeader("PROPERTIES"))
        props_frame = QFrame()
        props_frame.setObjectName("propertiesFrame")
        props_layout = QVBoxLayout(props_frame)
        props_layout.setContentsMargins(6, 6, 6, 6)
        props_layout.setSpacing(4)

        fit_row = QHBoxLayout()
        fit_row.setSpacing(4)
        fit_label = QLabel("Fit")
        fit_label.setObjectName("propTitle")
        fit_label.setFixedWidth(56)
        fit_row.addWidget(fit_label)
        self.btn_fit_w = QToolButton()
        self.btn_fit_w.setObjectName("mixerToolBtn")
        self.btn_fit_w.setText("W")
        self.btn_fit_w.setToolTip("Stretch to screen width (keep aspect)")
        self.btn_fit_h = QToolButton()
        self.btn_fit_h.setObjectName("mixerToolBtn")
        self.btn_fit_h.setText("H")
        self.btn_fit_h.setToolTip("Stretch to screen height (keep aspect)")
        self.btn_fit_full = QToolButton()
        self.btn_fit_full.setObjectName("mixerToolBtn")
        self.btn_fit_full.setText("FULL")
        self.btn_fit_full.setToolTip("Stretch to full screen")
        self.btn_fit_w.clicked.connect(lambda: self._fit_selected("width"))
        self.btn_fit_h.clicked.connect(lambda: self._fit_selected("height"))
        self.btn_fit_full.clicked.connect(lambda: self._fit_selected("screen"))
        fit_row.addWidget(self.btn_fit_w)
        fit_row.addWidget(self.btn_fit_h)
        fit_row.addWidget(self.btn_fit_full)
        fit_row.addStretch(1)
        props_layout.addLayout(fit_row)

        self.pos_x = self._prop_row(props_layout, "Position", "X", -99999, 99999, 0, 1.0, 1)
        self.pos_y = self._prop_row(props_layout, None, "Y", -99999, 99999, 0, 1.0, 1)
        self.rot = self._prop_row(props_layout, "Rotation", "", -3600, 3600, 0, 1.0, 1)
        self.scale_x = self._prop_row(props_layout, "Scale", "X", 0.01, 100, 1.0, 0.1, 2)
        self.scale_y = self._prop_row(props_layout, None, "Y", 0.01, 100, 1.0, 0.1, 2)
        self.anchor_x = self._prop_row(props_layout, "Anchor", "X", 0, 1, 0.5, 0.05, 2)
        self.anchor_y = self._prop_row(props_layout, None, "Y", 0, 1, 0.5, 0.05, 2)

        # Video-only controls (hidden for images)
        self.video_props = QWidget()
        video_col = QVBoxLayout(self.video_props)
        video_col.setContentsMargins(0, 0, 0, 0)
        video_col.setSpacing(4)

        self.playback_group = SegmentButtonGroup([("loop", "LOOP"), ("stop", "STOP")])
        video_col.addWidget(self.playback_group)

        main_seek_row = QHBoxLayout()
        main_seek_row.setSpacing(4)
        main_seek_label = QLabel("On Main")
        main_seek_label.setObjectName("propTitle")
        main_seek_label.setFixedWidth(56)
        main_seek_label.setToolTip("When scene goes to Main Screen")
        main_seek_row.addWidget(main_seek_label)
        self.main_seek_group = SegmentButtonGroup([("start", "START"), ("current", "CUR")])
        self.main_seek_group.setToolTip("Play from start or keep current position on Main")
        main_seek_row.addWidget(self.main_seek_group, 1)
        video_col.addLayout(main_seek_row)

        loop_tr_row = QHBoxLayout()
        loop_tr_row.setSpacing(4)
        loop_tr_label = QLabel("Loop")
        loop_tr_label.setObjectName("propTitle")
        loop_tr_label.setFixedWidth(56)
        loop_tr_label.setToolTip("Transition when looping back to start")
        loop_tr_row.addWidget(loop_tr_label)
        self.loop_transition_group = SegmentButtonGroup([("cut", "CUT"), ("fade", "FADE")])
        self.loop_transition_group.setToolTip("Hard cut or fade on loop wrap")
        loop_tr_row.addWidget(self.loop_transition_group, 1)
        video_col.addLayout(loop_tr_row)

        loop_fade_row = QHBoxLayout()
        loop_fade_row.setSpacing(4)
        loop_fade_label = QLabel("Loop ms")
        loop_fade_label.setObjectName("propTitle")
        loop_fade_label.setFixedWidth(56)
        loop_fade_row.addWidget(loop_fade_label)
        self.loop_fade_ms = FloatValueStepper(
            self, 0, 10000, 400, step=50, decimals=0, value_width=64
        )
        self.loop_fade_ms.setToolTip("Loop fade duration in milliseconds")
        loop_fade_row.addWidget(self.loop_fade_ms, 1)
        video_col.addLayout(loop_fade_row)

        seek_row = QHBoxLayout()
        self.time_current = QLabel("0:00")
        self.time_current.setObjectName("mixerTimeLabel")
        self.time_total = QLabel("0:00")
        self.time_total.setObjectName("mixerTimeLabel")
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("mixerSeek")
        self.seek_slider.setRange(0, 0)
        seek_row.addWidget(self.time_current)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.time_total)
        video_col.addLayout(seek_row)

        props_layout.addWidget(self.video_props)
        props_layout.addStretch(1)

        props_scroll = QScrollArea()
        props_scroll.setObjectName("propertiesScroll")
        props_scroll.setWidgetResizable(True)
        props_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        props_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        props_scroll.setFrameShape(QFrame.Shape.NoFrame)
        props_scroll.setWidget(props_frame)
        props_col.addWidget(props_scroll, 1)

        self.bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.bottom_splitter.setObjectName("mixerBottomSplitter")
        self.bottom_splitter.setChildrenCollapsible(False)
        self.bottom_splitter.addWidget(layers_panel)
        self.bottom_splitter.addWidget(props_panel)
        self.bottom_splitter.setStretchFactor(0, 1)
        self.bottom_splitter.setStretchFactor(1, 1)

        self.panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self.panel_splitter.setObjectName("mixerPanelSplitter")
        self.panel_splitter.setChildrenCollapsible(False)
        self.panel_splitter.addWidget(screens)
        self.panel_splitter.addWidget(scenes_section)
        self.panel_splitter.addWidget(self.bottom_splitter)
        self.panel_splitter.setStretchFactor(0, 3)
        self.panel_splitter.setStretchFactor(1, 1)
        self.panel_splitter.setStretchFactor(2, 2)
        root.addWidget(self.panel_splitter, 1)

        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self._wire_property_signals()
        self.preview_gl.transformEdited.connect(self.refresh_properties)
        self.model.changed.connect(self.refresh_all)
        self.model.layerTransformChanged.connect(self._on_transform_changed)
        self.model.layerSelected.connect(self._on_layer_selected)
        self.panel_splitter.splitterMoved.connect(self._persist_splitters)
        self.bottom_splitter.splitterMoved.connect(self._persist_splitters)
        self.refresh_all()
        self._restore_splitters()

    def _load_frozen_thumbs(self) -> None:
        folder = _thumbs_dir(self.model)
        for path in folder.glob("*.png"):
            pix = QPixmap(str(path))
            if not pix.isNull():
                self._frozen_thumbs[path.stem] = pix

    def reload_thumbs(self) -> None:
        self._frozen_thumbs.clear()
        self._load_frozen_thumbs()

    def store_frozen_thumb(self, scene_id: str, pixmap: QPixmap) -> None:
        if pixmap is None or pixmap.isNull():
            return
        self._frozen_thumbs[scene_id] = QPixmap(pixmap)
        out = _thumbs_dir(self.model) / f"{scene_id}.png"
        try:
            pixmap.save(str(out), "PNG")
        except Exception:
            pass
        slot = self._scene_slots_by_id.get(scene_id)
        if slot is not None:
            scene = self.model.scene_by_id(scene_id)
            if scene is not None:
                slot.set_scene(
                    scene.id,
                    scene.name,
                    is_main=scene.id == self.model.main_scene_id,
                    is_preview=scene.id == self.model.preview_scene_id,
                    pixmap=self._frozen_thumbs[scene_id],
                )

    def _persist_splitters(self, *_args) -> None:
        self.model.splitter_panel = self.panel_splitter.sizes()
        self.model.splitter_bottom = self.bottom_splitter.sizes()
        self.model.panel_width = max(280, self.width())

    def _restore_splitters(self) -> None:
        if self.model.splitter_panel and len(self.model.splitter_panel) == 3:
            self.panel_splitter.setSizes(self.model.splitter_panel)
        if self.model.splitter_bottom and len(self.model.splitter_bottom) == 2:
            self.bottom_splitter.setSizes(self.model.splitter_bottom)

    def _on_layers_reordered(self, layer_ids: list) -> None:
        if not layer_ids:
            return
        self.model.reorder_layers_top_to_bottom(list(layer_ids))

    def _on_transform_changed(self, _layer_id: str) -> None:
        self.refresh_properties()
        self.preview_gl.update()

    def _on_layer_selected(self, layer_id: object) -> None:
        self.refresh_properties()
        if isinstance(layer_id, str) or layer_id is None:
            self.layer_list.set_selected_layer(layer_id)
        self.refresh_views()

    def _screen_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("screenCaption")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def set_program_clock_ms(self, ms: int) -> None:
        total = max(0, int(ms) // 1000)
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if self.program_clock.text() != text:
            self.program_clock.setText(text)

    def _prop_row(
        self,
        parent_layout: QVBoxLayout,
        title: str | None,
        axis: str,
        minimum: float,
        maximum: float,
        value: float,
        step: float,
        decimals: int,
    ) -> FloatValueStepper:
        row = QHBoxLayout()
        row.setSpacing(4)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("propTitle")
            title_label.setFixedWidth(56)
            row.addWidget(title_label)
        else:
            row.addSpacing(56)
        if axis:
            axis_label = QLabel(axis)
            axis_label.setObjectName("propAxis")
            axis_label.setFixedWidth(12)
            row.addWidget(axis_label)
        stepper = FloatValueStepper(
            self,
            minimum,
            maximum,
            value,
            step=step,
            decimals=decimals,
            value_width=64,
        )
        row.addWidget(stepper, 1)
        parent_layout.addLayout(row)
        return stepper

    def _wire_property_signals(self) -> None:
        self.canvas_w.valueChanged.connect(self._on_canvas_size_changed)
        self.canvas_h.valueChanged.connect(self._on_canvas_size_changed)
        self.transition_ms.valueChanged.connect(self._on_transition_ms_changed)
        self.pos_x.valueChanged.connect(lambda v: self._apply_transform(x=v))
        self.pos_y.valueChanged.connect(lambda v: self._apply_transform(y=v))
        self.rot.valueChanged.connect(lambda v: self._apply_transform(rotation=v))
        self.scale_x.valueChanged.connect(lambda v: self._apply_transform(scale_x=v))
        self.scale_y.valueChanged.connect(lambda v: self._apply_transform(scale_y=v))
        self.anchor_x.valueChanged.connect(lambda v: self._apply_transform(anchor_x=v))
        self.anchor_y.valueChanged.connect(lambda v: self._apply_transform(anchor_y=v))
        self.playback_group.valueChanged.connect(self._on_playback_changed)
        self.main_seek_group.valueChanged.connect(self._on_main_seek_changed)
        self.loop_transition_group.valueChanged.connect(self._on_loop_transition_changed)
        self.loop_fade_ms.valueChanged.connect(self._on_loop_fade_ms_changed)
        self.seek_slider.sliderMoved.connect(self._on_seek)
        self.seek_slider.sliderReleased.connect(lambda: self._on_seek(self.seek_slider.value()))

    def _on_canvas_size_changed(self, _value: float = 0.0) -> None:
        if self._updating_props:
            return
        self.model.set_canvas_size(int(self.canvas_w.value()), int(self.canvas_h.value()))

    def _on_transition_ms_changed(self, value: float = 0.0) -> None:
        if self._updating_props:
            return
        self.model.set_transition_ms(int(value))

    def _fit_selected(self, mode: str) -> None:
        layer = self.model.selected_layer()
        if layer is None:
            return
        media = self.media_store.get(layer.id)
        if media is not None and media.width > 0 and media.height > 0:
            self.model.fit_layer_with_size(layer.id, media.width, media.height, mode)
        else:
            self.model.fit_layer(layer.id, mode)
        self.refresh_properties()
        self.refresh_views()

    def _apply_transform(self, **kwargs) -> None:
        if self._updating_props:
            return
        layer = self.model.selected_layer()
        if layer is None:
            return
        self.model.update_transform(layer.id, **kwargs)

    def _on_playback_changed(self, value: str) -> None:
        if self._updating_props:
            return
        layer = self.model.selected_layer()
        if layer is None:
            return
        mode = "stop" if value == "stop" else "loop"
        self.model.set_playback(layer.id, mode)

    def _on_main_seek_changed(self, value: str) -> None:
        if self._updating_props:
            return
        layer = self.model.selected_layer()
        if layer is None:
            return
        self.model.set_main_seek(layer.id, "current" if value == "current" else "start")

    def _on_loop_transition_changed(self, value: str) -> None:
        if self._updating_props:
            return
        layer = self.model.selected_layer()
        if layer is None:
            return
        self.model.set_loop_transition(layer.id, "fade" if value == "fade" else "cut")

    def _on_loop_fade_ms_changed(self, value: float = 0.0) -> None:
        if self._updating_props:
            return
        layer = self.model.selected_layer()
        if layer is None:
            return
        self.model.set_loop_fade_ms(layer.id, int(value))

    def _on_seek(self, value: int) -> None:
        if self._updating_props:
            return
        layer = self.model.selected_layer()
        if layer is None or layer.kind != "video":
            return
        self.model.set_layer_position_ms(layer.id, value)
        self.media_store.seek_layer(layer.id, value)
        self.time_current.setText(_format_ms(value))

    def _prompt_rename_scene(self, scene_id: str, current_name: str = "") -> None:
        scene = self.model.scene_by_id(scene_id)
        if scene is None:
            return
        initial = current_name or scene.name
        text, ok = QInputDialog.getText(self, "Rename scene", "Name:", text=initial)
        if ok and text.strip():
            self.model.rename_scene(scene_id, text.strip())

    def _rename_preview_scene(self) -> None:
        scene = self.model.preview_scene()
        if scene is None:
            return
        self._prompt_rename_scene(scene.id, scene.name)

    def _duplicate_preview_scene(self) -> None:
        source = self.model.preview_scene() or self.model.main_scene()
        if source is None:
            return
        if len(self.model.scenes) >= MAX_SCENES:
            QMessageBox.information(self, "Video Mixer", "Scene limit reached.")
            return
        source_id = source.id
        scene = self.model.duplicate_scene(source_id)
        if scene is None:
            return
        thumb = self._frozen_thumbs.get(source_id)
        if thumb is not None and not thumb.isNull():
            self.store_frozen_thumb(scene.id, thumb)
        self.media_store.sync()

    def _delete_preview_scene(self) -> None:
        scene = self.model.preview_scene()
        if scene is None:
            return
        if len(self.model.scenes) <= 1:
            QMessageBox.information(self, "Video Mixer", "Cannot delete the last scene.")
            return
        answer = QMessageBox.question(
            self,
            "Delete scene",
            f"Delete scene «{scene.name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        sid = scene.id
        self.model.remove_scene(sid)
        self._frozen_thumbs.pop(sid, None)
        thumb_path = _thumbs_dir(self.model) / f"{sid}.png"
        try:
            if thumb_path.exists():
                thumb_path.unlink()
        except Exception:
            pass

    def _delete_layer(self, layer_id: str) -> None:
        layer = self.model.find_layer(layer_id)
        if layer is None:
            return
        self.model.remove_layer(layer_id)

    def _browse_add_layer(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add layer",
            "",
            "Media (*.mp4 *.mov *.webm *.mkv *.avi *.jpg *.jpeg *.png *.webp *.bmp);;All files (*)",
        )
        if path:
            self.model.add_layer(path)
            self.media_store.sync()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and is_media_file(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        added = False
        for url in event.mimeData().urls():
            if url.isLocalFile() and is_media_file(url.toLocalFile()):
                self.model.add_layer(url.toLocalFile())
                added = True
        if added:
            self.media_store.sync()
            event.acceptProposedAction()
        else:
            event.ignore()

    def refresh_all(self) -> None:
        self.media_store.sync()
        self._refresh_scenes()
        self._refresh_layers()
        self.refresh_properties()
        has_preview = self.model.preview_scene() is not None
        self.btn_delete_scene.setEnabled(has_preview and len(self.model.scenes) > 1)
        self.btn_rename_scene.setEnabled(has_preview)
        self.btn_duplicate_scene.setEnabled(len(self.model.scenes) < MAX_SCENES)
        self.btn_clear_preview.setEnabled(has_preview)
        self.main_gl.update()
        self.preview_gl.update()

    def refresh_views(self) -> None:
        self._refresh_layer_missing_flags()
        self.main_gl.update()
        self.preview_gl.update()

    def _refresh_layer_missing_flags(self) -> None:
        """Update red missing-file state without rebuilding the list."""
        if self.layer_list.is_dragging():
            return
        scene = self.model.preview_scene()
        if scene is None:
            return
        by_id = {layer.id: layer for layer in scene.layers}
        changed = False
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            if item is None:
                continue
            lid = item.data(Qt.ItemDataRole.UserRole)
            layer = by_id.get(lid) if isinstance(lid, str) else None
            if layer is None:
                continue
            missing = layer.file_missing
            if bool(item.data(Qt.ItemDataRole.UserRole + 4)) != missing:
                item.setData(Qt.ItemDataRole.UserRole + 4, missing)
                changed = True
        if changed:
            self.layer_list.viewport().update()
            self.media_store.sync()
            self.main_gl.update()
            self.preview_gl.update()

    def update_seek_readout(self) -> None:
        layer = self.model.selected_layer()
        if layer is None or layer.kind != "video":
            return
        self._updating_props = True
        duration = max(0, layer.duration_ms)
        if self.seek_slider.maximum() != max(1, duration):
            self.seek_slider.setRange(0, max(1, duration))
        self.seek_slider.setValue(min(layer.position_ms, duration))
        self.time_current.setText(_format_ms(layer.position_ms))
        self.time_total.setText(_format_ms(duration))
        self._updating_props = False

    def refresh_properties(self) -> None:
        layer = self.model.selected_layer()
        self._updating_props = True
        self.canvas_w.set_value(float(self.model.canvas_width))
        self.canvas_h.set_value(float(self.model.canvas_height))
        self.transition_ms.set_value(float(self.model.transition_ms))
        enabled = layer is not None
        for w in (
            self.pos_x,
            self.pos_y,
            self.rot,
            self.scale_x,
            self.scale_y,
            self.anchor_x,
            self.anchor_y,
            self.btn_fit_w,
            self.btn_fit_h,
            self.btn_fit_full,
        ):
            w.setEnabled(enabled)

        if layer is None:
            self.video_props.hide()
            self.time_current.setText("0:00")
            self.time_total.setText("0:00")
            self.seek_slider.setRange(0, 0)
            self._updating_props = False
            return

        t = layer.transform
        self.pos_x.set_value(t.x)
        self.pos_y.set_value(t.y)
        self.rot.set_value(t.rotation)
        self.scale_x.set_value(t.scale_x)
        self.scale_y.set_value(t.scale_y)
        self.anchor_x.set_value(t.anchor_x)
        self.anchor_y.set_value(t.anchor_y)

        is_video = layer.kind == "video"
        self.video_props.setVisible(is_video)
        if is_video:
            self.playback_group.setEnabled(True)
            self.playback_group.set_value(layer.playback)
            self.main_seek_group.setEnabled(True)
            self.main_seek_group.set_value(layer.main_seek)
            self.loop_transition_group.setEnabled(layer.playback == "loop")
            self.loop_transition_group.set_value(layer.loop_transition)
            self.loop_fade_ms.setEnabled(
                layer.playback == "loop" and layer.loop_transition == "fade"
            )
            self.loop_fade_ms.set_value(float(layer.loop_fade_ms))
            duration = max(0, layer.duration_ms)
            self.seek_slider.setEnabled(True)
            self.seek_slider.setRange(0, max(1, duration))
            self.seek_slider.setValue(min(layer.position_ms, duration))
            self.time_current.setText(_format_ms(layer.position_ms))
            self.time_total.setText(_format_ms(duration))

        self._updating_props = False
        self.layer_list.set_selected_layer(layer.id)

    def _scene_column_count(self) -> int:
        viewport = self.scenes_scroll.viewport()
        width = viewport.width() if viewport is not None else 0
        if width <= 0:
            width = max(1, self.scenes_scroll.width() - 20)
        margins = self.scenes_grid.contentsMargins()
        spacing = max(0, self.scenes_grid.horizontalSpacing())
        avail = max(1, width - margins.left() - margins.right())
        slot_w = 68
        cols = max(1, (avail + spacing) // (slot_w + spacing))
        while cols > 1 and cols * slot_w + (cols - 1) * spacing > avail:
            cols -= 1
        return cols

    def _on_scenes_resized(self) -> None:
        # Debounce: avoid flicker from rapid width changes during splitter drag.
        self._relayout_timer.start()

    def _relayout_scenes(self) -> None:
        cols = self._scene_column_count()
        if cols == self._scenes_cols:
            return
        self._scenes_cols = cols
        widgets = list(self.scene_slots)
        if self._add_slot is not None:
            widgets.append(self._add_slot)
        for i, slot in enumerate(widgets):
            self.scenes_grid.addWidget(slot, i // cols, i % cols)

    def _make_scene_slot(self) -> SceneSlot:
        slot = SceneSlot(self)
        slot.mainRequested.connect(self.mainSceneRequested.emit)
        slot.previewRequested.connect(self.model.set_preview_scene)
        return slot

    def _refresh_scenes(self) -> None:
        """Update scene slots in place (no full rebuild → no flicker)."""
        scenes = self.model.scenes
        wanted_ids = [s.id for s in scenes]
        # Remove obsolete
        for sid in list(self._scene_slots_by_id.keys()):
            if sid not in wanted_ids:
                slot = self._scene_slots_by_id.pop(sid)
                self.scenes_grid.removeWidget(slot)
                slot.deleteLater()
        # Ensure slots exist / update
        self.scene_slots = []
        for scene in scenes:
            slot = self._scene_slots_by_id.get(scene.id)
            if slot is None:
                slot = self._make_scene_slot()
                self._scene_slots_by_id[scene.id] = slot
            slot.set_scene(
                scene.id,
                scene.name,
                is_main=scene.id == self.model.main_scene_id,
                is_preview=scene.id == self.model.preview_scene_id,
                pixmap=self._scene_thumb(scene.id),
            )
            self.scene_slots.append(slot)

        if len(scenes) < MAX_SCENES:
            if self._add_slot is None:
                self._add_slot = SceneSlot(self)
                self._add_slot.addRequested.connect(self.model.add_scene)
            self._add_slot.set_add()
        else:
            if self._add_slot is not None:
                self.scenes_grid.removeWidget(self._add_slot)
                self._add_slot.deleteLater()
                self._add_slot = None

        self._scenes_cols = 0  # force relayout
        self._relayout_scenes()

    def _scene_thumb(self, scene_id: str) -> QPixmap | None:
        frozen = self._frozen_thumbs.get(scene_id)
        if frozen is not None and not frozen.isNull():
            return frozen
        scene = self.model.scene_by_id(scene_id)
        if scene is None:
            return None
        for layer in reversed(scene.layers):
            if not layer.visible or layer.kind != "image" or layer.file_missing:
                continue
            pix = QPixmap(layer.path)
            if not pix.isNull():
                return pix
        return None

    def _refresh_layers(self) -> None:
        # Rebuild during drag deletes QListWidgetItem while startDrag still holds it.
        if self.layer_list.is_dragging():
            return
        scene = self.model.preview_scene()
        if scene is None:
            self.layer_list.set_layers([], None)
            return
        rows = [
            (
                layer.id,
                layer.name,
                layer.visible,
                layer.muted,
                layer.kind == "video",
                layer.file_missing,
            )
            for layer in reversed(scene.layers)
        ]
        self.layer_list.set_layers(rows, self.model.selected_layer_id)

    def release_gl(self) -> None:
        self.main_gl.release()
        self.preview_gl.release()
