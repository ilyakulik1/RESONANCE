from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QObject, pyqtSignal

from app.config import get_config_path
from app.constants import FADE_PRESETS_MS, VIDEO_TRANSITION_PRESET_SHORTCUTS, fade_preset_label
from app.video_mixer.media_utils import media_kind

PlaybackMode = Literal["loop", "stop"]
LayerKind = Literal["video", "image"]
MainSeekMode = Literal["start", "current"]
LoopTransition = Literal["cut", "fade"]

DEFAULT_CANVAS_WIDTH = 1920
DEFAULT_CANVAS_HEIGHT = 1080
MAX_SCENES = 64
DEFAULT_LOOP_FADE_MS = 400
DEFAULT_TRANSITION_MS = 400
TRANSITION_PRESETS_MS = FADE_PRESETS_MS
TRANSITION_PRESET_SHORTCUTS = VIDEO_TRANSITION_PRESET_SHORTCUTS
transition_preset_label = fade_preset_label


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class LayerTransform:
    x: float = 960.0
    y: float = 540.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> LayerTransform:
        if not isinstance(data, dict):
            return cls()
        return cls(
            x=float(data.get("x", 960.0)),
            y=float(data.get("y", 540.0)),
            rotation=float(data.get("rotation", 0.0)),
            scale_x=float(data.get("scale_x", 1.0)),
            scale_y=float(data.get("scale_y", 1.0)),
            anchor_x=float(data.get("anchor_x", 0.5)),
            anchor_y=float(data.get("anchor_y", 0.5)),
        )


@dataclass
class OutputMapping:
    """Projection map of the main canvas onto the physical output (normalized 0..1).

    Corners are TL, TR, BR, BL in output-window space. Identity = full screen.
    """

    tl: tuple[float, float] = (0.0, 0.0)
    tr: tuple[float, float] = (1.0, 0.0)
    br: tuple[float, float] = (1.0, 1.0)
    bl: tuple[float, float] = (0.0, 1.0)
    enabled: bool = True

    @staticmethod
    def identity() -> OutputMapping:
        return OutputMapping()

    def corners(self) -> list[tuple[float, float]]:
        return [self.tl, self.tr, self.br, self.bl]

    def set_corner(self, index: int, x: float, y: float) -> None:
        pt = (float(x), float(y))
        if index == 0:
            self.tl = pt
        elif index == 1:
            self.tr = pt
        elif index == 2:
            self.br = pt
        elif index == 3:
            self.bl = pt

    def is_identity(self, eps: float = 1e-4) -> bool:
        expected = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for (ax, ay), (bx, by) in zip(self.corners(), expected):
            if abs(ax - bx) > eps or abs(ay - by) > eps:
                return False
        return True

    def reset(self) -> None:
        self.tl = (0.0, 0.0)
        self.tr = (1.0, 0.0)
        self.br = (1.0, 1.0)
        self.bl = (0.0, 1.0)

    def scale_about_center(self, factor: float) -> None:
        """Uniform scale of the mapped quad around its centroid."""
        factor = max(0.05, float(factor))
        pts = self.corners()
        cx = sum(p[0] for p in pts) / 4.0
        cy = sum(p[1] for p in pts) / 4.0
        scaled = [((x - cx) * factor + cx, (y - cy) * factor + cy) for x, y in pts]
        self.tl, self.tr, self.br, self.bl = scaled  # type: ignore[misc]

    def scale_x_about_center(self, factor: float) -> None:
        """Horizontal scale around the quad centroid."""
        factor = max(0.05, float(factor))
        pts = self.corners()
        cx = sum(p[0] for p in pts) / 4.0
        scaled = [((x - cx) * factor + cx, y) for x, y in pts]
        self.tl, self.tr, self.br, self.bl = scaled  # type: ignore[misc]

    def scale_y_about_center(self, factor: float) -> None:
        """Vertical scale around the quad centroid."""
        factor = max(0.05, float(factor))
        pts = self.corners()
        cy = sum(p[1] for p in pts) / 4.0
        scaled = [(x, (y - cy) * factor + cy) for x, y in pts]
        self.tl, self.tr, self.br, self.bl = scaled  # type: ignore[misc]

    def translate(self, dx: float, dy: float) -> None:
        self.tl = (self.tl[0] + dx, self.tl[1] + dy)
        self.tr = (self.tr[0] + dx, self.tr[1] + dy)
        self.br = (self.br[0] + dx, self.br[1] + dy)
        self.bl = (self.bl[0] + dx, self.bl[1] + dy)

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "tl": list(self.tl),
            "tr": list(self.tr),
            "br": list(self.br),
            "bl": list(self.bl),
        }

    @staticmethod
    def _pair(data: dict, key: str, default: tuple[float, float]) -> tuple[float, float]:
        raw = data.get(key, default)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return float(raw[0]), float(raw[1])
        return default

    @classmethod
    def from_dict(cls, data: dict | None) -> OutputMapping:
        if not isinstance(data, dict):
            return cls.identity()
        return cls(
            tl=cls._pair(data, "tl", (0.0, 0.0)),
            tr=cls._pair(data, "tr", (1.0, 0.0)),
            br=cls._pair(data, "br", (1.0, 1.0)),
            bl=cls._pair(data, "bl", (0.0, 1.0)),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class Layer:
    id: str
    path: str
    kind: LayerKind
    visible: bool = True
    transform: LayerTransform = field(default_factory=LayerTransform)
    playback: PlaybackMode = "loop"
    position_ms: int = 0
    duration_ms: int = 0
    playing: bool = True
    muted: bool = True  # video audio off by default
    # When scene is taken to Main: restart video or keep scrub position
    main_seek: MainSeekMode = "start"
    # On loop wrap: hard cut or fade through last→first frame
    loop_transition: LoopTransition = "cut"
    loop_fade_ms: int = DEFAULT_LOOP_FADE_MS

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def file_missing(self) -> bool:
        """True when the media file is not present on disk."""
        return not self.path or not Path(self.path).is_file()
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "kind": self.kind,
            "visible": self.visible,
            "transform": self.transform.to_dict(),
            "playback": self.playback,
            "position_ms": self.position_ms,
            "duration_ms": self.duration_ms,
            "playing": self.playing,
            "muted": self.muted,
            "main_seek": self.main_seek,
            "loop_transition": self.loop_transition,
            "loop_fade_ms": self.loop_fade_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Layer | None:
        path = data.get("path")
        kind = data.get("kind")
        if not isinstance(path, str) or kind not in ("video", "image"):
            return None
        main_seek = data.get("main_seek", "start")
        if main_seek not in ("start", "current"):
            main_seek = "start"
        loop_tr = data.get("loop_transition", "cut")
        if loop_tr not in ("cut", "fade"):
            loop_tr = "cut"
        return cls(
            id=str(data.get("id") or _new_id()),
            path=path,
            kind=kind,
            visible=bool(data.get("visible", True)),
            transform=LayerTransform.from_dict(data.get("transform")),
            playback="stop" if data.get("playback") == "stop" else "loop",
            position_ms=int(data.get("position_ms", 0)),
            duration_ms=int(data.get("duration_ms", 0)),
            playing=bool(data.get("playing", True)),
            muted=bool(data.get("muted", True)),
            main_seek=main_seek,
            loop_transition=loop_tr,
            loop_fade_ms=max(0, min(10000, int(data.get("loop_fade_ms", DEFAULT_LOOP_FADE_MS)))),
        )

    @classmethod
    def from_path(cls, path: str | Path) -> Layer | None:
        path = Path(path)
        kind = media_kind(path)
        if kind is None or not path.is_file():
            return None
        return cls(
            id=_new_id(),
            path=str(path.resolve()),
            kind=kind,
            transform=LayerTransform(),
        )


@dataclass
class Scene:
    id: str
    name: str
    layers: list[Layer] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Scene | None:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        layers: list[Layer] = []
        raw_layers = data.get("layers")
        if isinstance(raw_layers, list):
            for item in raw_layers:
                if isinstance(item, dict):
                    layer = Layer.from_dict(item)
                    if layer is not None:
                        layers.append(layer)
        return cls(
            id=str(data.get("id") or _new_id()),
            name=name.strip(),
            layers=layers,
        )

    @classmethod
    def create(cls, name: str = "Scene") -> Scene:
        return cls(id=_new_id(), name=name, layers=[])


class MixerModel(QObject):
    """Scenes / layers / selection; emits change signals for UI and renderer."""

    changed = pyqtSignal()
    sceneActivated = pyqtSignal(str)
    layerSelected = pyqtSignal(object)  # layer id or None
    layerTransformChanged = pyqtSignal(str)
    playbackChanged = pyqtSignal(str)
    outputMappingChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas_width = DEFAULT_CANVAS_WIDTH
        self.canvas_height = DEFAULT_CANVAS_HEIGHT
        self.scenes: list[Scene] = [Scene.create("Scene 1")]
        # Main = program out (red). Preview = edit/load (RMB); None = unloaded.
        self.main_scene_id: str = self.scenes[0].id
        self.preview_scene_id: str | None = self.scenes[0].id
        self.pending_main_scene_id: str | None = None
        # Frozen look for Main/output: preview edits (transform, visibility)
        # do not show until the scene is taken to Main again.
        self.program_look: Scene | None = None
        self.capture_program_look()
        self.selected_layer_id: str | None = None
        self.output_screen_name: str | None = None
        self.output_mapping: OutputMapping = OutputMapping.identity()
        self.panel_collapsed: bool = False
        self.panel_width: int = 360
        self.transition_ms: int = DEFAULT_TRANSITION_MS
        self.preload_ms: int = 200
        self.splitter_main: list[int] | None = None
        self.splitter_panel: list[int] | None = None
        self.splitter_bottom: list[int] | None = None
        # Set by VideoMixerController when a project is active.
        self.project_root: Path | None = None

    @property
    def active_scene_id(self) -> str | None:
        """Backward-compatible alias: editing happens on preview."""
        return self.preview_scene_id

    def scene_by_id(self, scene_id: str | None) -> Scene | None:
        if not scene_id:
            return None
        for scene in self.scenes:
            if scene.id == scene_id:
                return scene
        return None

    def main_scene(self) -> Scene | None:
        scene = self.scene_by_id(self.main_scene_id)
        return scene or (self.scenes[0] if self.scenes else None)

    def program_scene(self) -> Scene | None:
        """Scene look currently committed to Main / physical output."""
        return self.program_look or self.main_scene()

    def capture_program_look(self) -> None:
        """Commit the current main scene look (visibility / transform) to program."""
        scene = self.scene_by_id(self.main_scene_id) or (
            self.scenes[0] if self.scenes else None
        )
        if scene is None:
            self.program_look = None
            return
        copied = Scene.from_dict(scene.to_dict())
        self.program_look = copied

    def preview_scene(self) -> Scene | None:
        """Preview may be unloaded (None) to save decode load."""
        return self.scene_by_id(self.preview_scene_id)

    def active_scene(self) -> Scene | None:
        """Scene being edited (preview)."""
        return self.preview_scene()

    def pending_main_scene(self) -> Scene | None:
        return self.scene_by_id(self.pending_main_scene_id)

    def selected_layer(self) -> Layer | None:
        scene = self.preview_scene()
        if scene is None or self.selected_layer_id is None:
            return None
        for layer in scene.layers:
            if layer.id == self.selected_layer_id:
                return layer
        return None

    def find_layer(self, layer_id: str) -> Layer | None:
        for scene in self.scenes:
            for layer in scene.layers:
                if layer.id == layer_id:
                    return layer
        return None

    def _select_default_layer(self, scene: Scene | None) -> None:
        if scene and scene.layers:
            if not any(l.id == self.selected_layer_id for l in scene.layers):
                self.selected_layer_id = scene.layers[-1].id
        else:
            self.selected_layer_id = None

    def set_main_scene(self, scene_id: str) -> None:
        """Left-click: send scene to Main Screen / physical output."""
        if not any(s.id == scene_id for s in self.scenes):
            return
        self.main_scene_id = scene_id
        self.capture_program_look()
        self.sceneActivated.emit(scene_id)
        self.changed.emit()

    def set_preview_scene(self, scene_id: str) -> None:
        """Right-click: load scene into Preview for editing."""
        if not any(s.id == scene_id for s in self.scenes):
            return
        self.preview_scene_id = scene_id
        self._select_default_layer(self.preview_scene())
        self.sceneActivated.emit(scene_id)
        self.layerSelected.emit(self.selected_layer_id)
        self.changed.emit()

    def clear_preview(self) -> None:
        """Unload Preview to free decoders / reduce load."""
        if self.preview_scene_id is None and self.selected_layer_id is None:
            return
        self.preview_scene_id = None
        self.selected_layer_id = None
        self.layerSelected.emit(None)
        self.changed.emit()

    def set_transition_ms(self, ms: int) -> None:
        self.transition_ms = max(0, min(10000, int(ms)))

    def set_preload_ms(self, ms: int) -> None:
        self.preload_ms = max(0, min(5000, int(ms)))

    def activate_scene(self, scene_id: str) -> None:
        """Deprecated helper: set both main and preview."""
        self.set_main_scene(scene_id)
        self.set_preview_scene(scene_id)

    def add_scene(self, name: str | None = None) -> Scene | None:
        if len(self.scenes) >= MAX_SCENES:
            return None
        index = len(self.scenes) + 1
        scene = Scene.create(name or f"Scene {index}")
        self.scenes.append(scene)
        self.set_preview_scene(scene.id)
        return scene

    def _unique_scene_name(self, base: str) -> str:
        existing = {scene.name for scene in self.scenes}
        candidate = f"{base} copy"
        if candidate not in existing:
            return candidate
        n = 2
        while f"{base} copy {n}" in existing:
            n += 1
        return f"{base} copy {n}"

    def duplicate_scene(self, scene_id: str | None = None) -> Scene | None:
        """Deep-copy a scene (layers + transforms) with new ids."""
        if len(self.scenes) >= MAX_SCENES:
            return None
        source = self.scene_by_id(scene_id) or self.preview_scene()
        if source is None:
            return None
        data = source.to_dict()
        data["id"] = _new_id()
        data["name"] = self._unique_scene_name(source.name)
        raw_layers = data.get("layers")
        if isinstance(raw_layers, list):
            for item in raw_layers:
                if isinstance(item, dict):
                    item["id"] = _new_id()
        scene = Scene.from_dict(data)
        if scene is None:
            return None
        insert_at = next(
            (i + 1 for i, s in enumerate(self.scenes) if s.id == source.id),
            len(self.scenes),
        )
        self.scenes.insert(insert_at, scene)
        self.set_preview_scene(scene.id)
        return scene

    def rename_scene(self, scene_id: str, name: str) -> None:
        text = name.strip()
        if not text:
            return
        for scene in self.scenes:
            if scene.id == scene_id:
                scene.name = text
                self.changed.emit()
                return

    def remove_scene(self, scene_id: str) -> bool:
        """Remove a scene. Keeps at least one scene. Returns True if removed."""
        if len(self.scenes) <= 1:
            return False
        if not any(s.id == scene_id for s in self.scenes):
            return False

        self.scenes = [s for s in self.scenes if s.id != scene_id]
        fallback = self.scenes[0].id
        was_main = self.main_scene_id == scene_id
        if was_main:
            self.main_scene_id = fallback
        if self.preview_scene_id == scene_id:
            self.preview_scene_id = fallback
            self._select_default_layer(self.preview_scene())
            self.layerSelected.emit(self.selected_layer_id)
        if self.pending_main_scene_id == scene_id:
            self.pending_main_scene_id = None
        if was_main:
            self.capture_program_look()
        self.changed.emit()
        return True

    def select_layer(self, layer_id: str | None) -> None:
        self.selected_layer_id = layer_id
        self.layerSelected.emit(layer_id)

    def add_layer(self, path: str | Path) -> Layer | None:
        scene = self.preview_scene()
        if scene is None:
            return None
        layer = Layer.from_path(path)
        if layer is None:
            return None
        layer.transform.x = self.canvas_width / 2
        layer.transform.y = self.canvas_height / 2
        # Fit media into canvas so it is visible on import
        self._fit_layer_to_canvas(layer)
        scene.layers.append(layer)
        self.selected_layer_id = layer.id
        self.layerSelected.emit(layer.id)
        self.changed.emit()
        return layer

    def _fit_layer_to_canvas(self, layer: Layer) -> None:
        width = height = 0
        if layer.kind == "image":
            from PyQt6.QtGui import QImage

            image = QImage(layer.path)
            if not image.isNull():
                width, height = image.width(), image.height()
        if width <= 0 or height <= 0:
            return
        fit = min(
            (self.canvas_width * 0.9) / width,
            (self.canvas_height * 0.9) / height,
        )
        layer.transform.scale_x = fit
        layer.transform.scale_y = fit

    def set_canvas_size(self, width: int, height: int) -> None:
        w = max(16, min(16384, int(width)))
        h = max(16, min(16384, int(height)))
        if w == self.canvas_width and h == self.canvas_height:
            return
        self.canvas_width = w
        self.canvas_height = h
        self.changed.emit()

    def fit_layer(self, layer_id: str, mode: str) -> None:
        """mode: width | height | screen (stretch fill)."""
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        # Prefer live media size; fall back to image file
        width = height = 0
        # Caller may pass sizes via media store; resolve file for images
        if layer.kind == "image":
            from PyQt6.QtGui import QImage

            image = QImage(layer.path)
            if not image.isNull():
                width, height = image.width(), image.height()
        if width <= 0 or height <= 0:
            return
        self._apply_fit(layer, width, height, mode)

    def fit_layer_with_size(self, layer_id: str, media_w: int, media_h: int, mode: str) -> None:
        layer = self.find_layer(layer_id)
        if layer is None or media_w <= 0 or media_h <= 0:
            return
        self._apply_fit(layer, media_w, media_h, mode)

    def _apply_fit(self, layer: Layer, media_w: int, media_h: int, mode: str) -> None:
        cw = float(self.canvas_width)
        ch = float(self.canvas_height)
        mw = float(media_w)
        mh = float(media_h)
        if mode == "width":
            s = cw / mw
            layer.transform.scale_x = s
            layer.transform.scale_y = s
        elif mode == "height":
            s = ch / mh
            layer.transform.scale_x = s
            layer.transform.scale_y = s
        else:  # screen — stretch to full canvas
            layer.transform.scale_x = cw / mw
            layer.transform.scale_y = ch / mh
        layer.transform.x = cw / 2.0
        layer.transform.y = ch / 2.0
        layer.transform.anchor_x = 0.5
        layer.transform.anchor_y = 0.5
        layer.transform.rotation = 0.0
        self.layerTransformChanged.emit(layer.id)
        self.changed.emit()

    def remove_layer(self, layer_id: str) -> None:
        scene = self.preview_scene()
        if scene is None:
            return
        scene.layers = [l for l in scene.layers if l.id != layer_id]
        if self.selected_layer_id == layer_id:
            self.selected_layer_id = scene.layers[-1].id if scene.layers else None
            self.layerSelected.emit(self.selected_layer_id)
        self.changed.emit()

    def reorder_layers_top_to_bottom(self, layer_ids: list[str]) -> None:
        """Reorder preview layers from UI order (top → bottom)."""
        scene = self.preview_scene()
        if scene is None:
            return
        by_id = {layer.id: layer for layer in scene.layers}
        ordered: list[Layer] = []
        for layer_id in reversed(layer_ids):
            layer = by_id.pop(layer_id, None)
            if layer is not None:
                ordered.append(layer)
        # Keep any leftover at bottom
        ordered = list(by_id.values()) + ordered
        scene.layers = ordered
        self.changed.emit()

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        layer.visible = visible
        self.changed.emit()

    def set_layer_muted(self, layer_id: str, muted: bool) -> None:
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        layer.muted = bool(muted)
        if self.program_look is not None:
            for prog_layer in self.program_look.layers:
                if prog_layer.id == layer_id:
                    prog_layer.muted = bool(muted)
                    break
        self.changed.emit()

    def main_scene_has_audible_video(self) -> bool:
        scene = self.program_scene()
        if scene is None:
            return False
        for layer in scene.layers:
            if (
                layer.kind == "video"
                and layer.visible
                and not layer.file_missing
                and not layer.muted
                and layer.playing
            ):
                return True
        return False

    def update_transform(self, layer_id: str, **kwargs) -> None:
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        t = layer.transform
        for key, value in kwargs.items():
            if hasattr(t, key):
                setattr(t, key, float(value))
        self.layerTransformChanged.emit(layer_id)

    def set_output_mapping(self, mapping: OutputMapping, *, emit_changed: bool = True) -> None:
        self.output_mapping = mapping
        self.outputMappingChanged.emit()
        if emit_changed:
            self.changed.emit()

    def update_output_mapping_corner(self, index: int, x: float, y: float) -> None:
        self.output_mapping.set_corner(index, x, y)
        self.outputMappingChanged.emit()

    def reset_output_mapping(self) -> None:
        self.output_mapping.reset()
        self.outputMappingChanged.emit()
        self.changed.emit()

    def scale_output_mapping(self, factor: float) -> None:
        self.output_mapping.scale_about_center(factor)
        self.outputMappingChanged.emit()

    def scale_output_mapping_x(self, factor: float) -> None:
        self.output_mapping.scale_x_about_center(factor)
        self.outputMappingChanged.emit()

    def scale_output_mapping_y(self, factor: float) -> None:
        self.output_mapping.scale_y_about_center(factor)
        self.outputMappingChanged.emit()

    def translate_output_mapping(self, dx: float, dy: float) -> None:
        self.output_mapping.translate(dx, dy)
        self.outputMappingChanged.emit()

    def set_playback(self, layer_id: str, mode: PlaybackMode) -> None:
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        layer.playback = mode
        layer.playing = True
        self.playbackChanged.emit(layer_id)
        self.changed.emit()

    def set_main_seek(self, layer_id: str, mode: MainSeekMode) -> None:
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        layer.main_seek = "current" if mode == "current" else "start"
        self.changed.emit()

    def set_loop_transition(self, layer_id: str, mode: LoopTransition) -> None:
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        layer.loop_transition = "fade" if mode == "fade" else "cut"
        self.changed.emit()

    def set_loop_fade_ms(self, layer_id: str, ms: int) -> None:
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        layer.loop_fade_ms = max(0, min(10000, int(ms)))

    def set_layer_position_ms(self, layer_id: str, position_ms: int) -> None:
        """Seek only — do not emit `changed` (avoids media rebuild / UI flicker)."""
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        layer.position_ms = max(0, int(position_ms))
        self.playbackChanged.emit(layer_id)

    def set_layer_duration_ms(self, layer_id: str, duration_ms: int) -> None:
        layer = self.find_layer(layer_id)
        if layer is None:
            return
        layer.duration_ms = max(0, int(duration_ms))

    def to_dict(self) -> dict:
        return {
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "main_scene_id": self.main_scene_id,
            "preview_scene_id": self.preview_scene_id,
            "active_scene_id": self.preview_scene_id,
            "selected_layer_id": self.selected_layer_id,
            "output_screen_name": self.output_screen_name,
            "output_mapping": self.output_mapping.to_dict(),
            "panel_collapsed": self.panel_collapsed,
            "panel_width": self.panel_width,
            "transition_ms": self.transition_ms,
            "preload_ms": self.preload_ms,
            "splitter_main": self.splitter_main,
            "splitter_panel": self.splitter_panel,
            "splitter_bottom": self.splitter_bottom,
            "scenes": [s.to_dict() for s in self.scenes],
        }

    def load_dict(self, data: dict) -> None:
        self.canvas_width = int(data.get("canvas_width", DEFAULT_CANVAS_WIDTH))
        self.canvas_height = int(data.get("canvas_height", DEFAULT_CANVAS_HEIGHT))
        self.output_screen_name = data.get("output_screen_name")
        if isinstance(self.output_screen_name, str) and not self.output_screen_name:
            self.output_screen_name = None
        self.output_mapping = OutputMapping.from_dict(data.get("output_mapping"))
        self.panel_collapsed = bool(data.get("panel_collapsed", False))
        self.panel_width = int(data.get("panel_width", 360))
        self.transition_ms = max(0, min(10000, int(data.get("transition_ms", DEFAULT_TRANSITION_MS))))
        self.preload_ms = max(0, min(5000, int(data.get("preload_ms", 200))))
        self.pending_main_scene_id = None

        def _sizes(key: str) -> list[int] | None:
            raw_sizes = data.get(key)
            if isinstance(raw_sizes, list) and all(isinstance(x, (int, float)) for x in raw_sizes):
                return [int(x) for x in raw_sizes]
            return None

        self.splitter_main = _sizes("splitter_main")
        self.splitter_panel = _sizes("splitter_panel")
        self.splitter_bottom = _sizes("splitter_bottom")

        scenes: list[Scene] = []
        raw = data.get("scenes")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    scene = Scene.from_dict(item)
                    if scene is not None:
                        scenes.append(scene)
        if not scenes:
            scenes = [Scene.create("Scene 1")]
        self.scenes = scenes[:MAX_SCENES]

        def _pick(key: str, fallback: str) -> str:
            value = data.get(key)
            if isinstance(value, str) and any(s.id == value for s in self.scenes):
                return value
            return fallback

        legacy = data.get("active_scene_id")
        legacy_id = (
            legacy
            if isinstance(legacy, str) and any(s.id == legacy for s in self.scenes)
            else self.scenes[0].id
        )
        self.main_scene_id = _pick("main_scene_id", legacy_id)
        preview_raw = data.get("preview_scene_id", data.get("active_scene_id"))
        if preview_raw is None or preview_raw == "":
            self.preview_scene_id = None
        else:
            self.preview_scene_id = _pick("preview_scene_id", legacy_id)

        selected = data.get("selected_layer_id")
        scene = self.preview_scene()
        if (
            isinstance(selected, str)
            and scene is not None
            and any(l.id == selected for l in scene.layers)
        ):
            self.selected_layer_id = selected
        else:
            self.selected_layer_id = scene.layers[-1].id if scene and scene.layers else None

        self.capture_program_look()
        self.changed.emit()
        if self.preview_scene_id:
            self.sceneActivated.emit(self.preview_scene_id)
        self.layerSelected.emit(self.selected_layer_id)

    def save(self, path: Path | None = None) -> None:
        path = path or get_config_path("video_mixer.json")
        try:
            path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            print(f"Error saving video mixer config: {exc}")

    def load(self, path: Path | None = None) -> None:
        path = path or get_config_path("video_mixer.json")
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.load_dict(data)
        except Exception as exc:
            print(f"Error loading video mixer config: {exc}")
