from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.video_mixer.media_store import LayerMedia, MediaStore
    from app.video_mixer.model import LayerTransform, MixerModel

VERTEX_SHADER = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
uniform mat4 u_mvp;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = u_mvp * vec4(in_pos, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330
uniform sampler2D u_tex;
uniform float u_opaque;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec4 c = texture(u_tex, v_uv);
    if (u_opaque > 0.5) {
        f_color = vec4(c.rgb, 1.0);
    } else {
        f_color = c;
    }
}
"""


def _identity() -> np.ndarray:
    return np.eye(4, dtype=np.float32)


def _translate(x: float, y: float, z: float = 0.0) -> np.ndarray:
    m = _identity()
    m[0, 3] = x
    m[1, 3] = y
    m[2, 3] = z
    return m


def _scale(sx: float, sy: float, sz: float = 1.0) -> np.ndarray:
    m = _identity()
    m[0, 0] = sx
    m[1, 1] = sy
    m[2, 2] = sz
    return m


def _rotate_z(degrees: float) -> np.ndarray:
    rad = math.radians(degrees)
    c = math.cos(rad)
    s = math.sin(rad)
    m = _identity()
    m[0, 0] = c
    m[0, 1] = -s
    m[1, 0] = s
    m[1, 1] = c
    return m


def _ortho(left: float, right: float, bottom: float, top: float, near: float = -1.0, far: float = 1.0) -> np.ndarray:
    m = _identity()
    m[0, 0] = 2.0 / (right - left)
    m[1, 1] = 2.0 / (top - bottom)
    m[2, 2] = -2.0 / (far - near)
    m[0, 3] = -(right + left) / (right - left)
    m[1, 3] = -(top + bottom) / (top - bottom)
    m[2, 3] = -(far + near) / (far - near)
    return m


def _mul(*mats: np.ndarray) -> np.ndarray:
    result = mats[0]
    for mat in mats[1:]:
        result = result @ mat
    return result


def layer_model_matrix(transform: LayerTransform, width: int, height: int) -> np.ndarray:
    """Map unit quad [0,1]² → canvas pixels (column-vector: M @ p)."""
    ax = transform.anchor_x * width
    ay = transform.anchor_y * height
    return _mul(
        _translate(transform.x, transform.y),
        _rotate_z(transform.rotation),
        _scale(transform.scale_x, transform.scale_y),
        _translate(-ax, -ay),
        _scale(float(width), float(height)),
    )


def layer_corners(transform: LayerTransform, width: int, height: int) -> list[tuple[float, float]]:
    m = layer_model_matrix(transform, width, height)
    local = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    out: list[tuple[float, float]] = []
    for x, y in local:
        v = m @ np.array([x, y, 0.0, 1.0], dtype=np.float32)
        out.append((float(v[0]), float(v[1])))
    return out


def layer_aabb(transform: LayerTransform, width: int, height: int) -> tuple[float, float, float, float]:
    corners = layer_corners(transform, width, height)
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


class GlCompositor:
    """ModernGL drawer for mixer layers (one instance per QOpenGLWidget context)."""

    def __init__(self):
        self.ctx = None
        self.prog = None
        self.quad_vbo = None
        self.quad_vao = None
        self._textures: dict = {}
        self._texture_sizes: dict[str, tuple[int, int]] = {}
        self._texture_generations: dict[str, int] = {}

    def initialize(self) -> None:
        import moderngl

        self.ctx = moderngl.create_context(require=330)
        self.prog = self.ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
        # Interleaved: x y u v  (origin top-left in UV for Qt-like images)
        vertices = np.array(
            [
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                1.0,
                0.0,
                0.0,
                1.0,
                0.0,
                1.0,
                1.0,
                1.0,
                1.0,
                1.0,
            ],
            dtype="f4",
        )
        self.quad_vbo = self.ctx.buffer(vertices.tobytes())
        self.quad_vao = self.ctx.vertex_array(
            self.prog,
            [(self.quad_vbo, "2f 2f", "in_pos", "in_uv")],
        )

    def release(self) -> None:
        for tex in self._textures.values():
            tex.release()
        self._textures.clear()
        self._texture_sizes.clear()
        self._texture_generations.clear()
        if self.quad_vao is not None:
            self.quad_vao.release()
            self.quad_vao = None
        if self.quad_vbo is not None:
            self.quad_vbo.release()
            self.quad_vbo = None
        if self.prog is not None:
            self.prog.release()
            self.prog = None
        self.ctx = None

    def _upload(self, layer_id: str, media: LayerMedia):
        if self.ctx is None or media.frame is None:
            return None
        frame = np.ascontiguousarray(media.frame)
        if frame.ndim != 3 or frame.shape[2] < 3:
            return None
        h, w = frame.shape[:2]
        # Prefer RGB upload for images (avoids broken alpha making layer invisible)
        if frame.shape[2] >= 4 and media.kind == "image":
            rgb = np.ascontiguousarray(frame[:, :, :3])
            components = 3
            payload = rgb
        elif frame.shape[2] >= 4:
            payload = frame[:, :, :4]
            components = 4
        else:
            payload = frame[:, :, :3]
            components = 3

        tex = self._textures.get(layer_id)
        size = self._texture_sizes.get(layer_id)
        gen = self._texture_generations.get(layer_id, -1)
        key_size = (w, h, components)
        if tex is None or size != key_size:
            if tex is not None:
                tex.release()
            tex = self.ctx.texture((w, h), components, payload.tobytes())
            tex.filter = (self.ctx.LINEAR, self.ctx.LINEAR)
            tex.repeat_x = False
            tex.repeat_y = False
            self._textures[layer_id] = tex
            self._texture_sizes[layer_id] = key_size
            self._texture_generations[layer_id] = media.generation
        elif gen != media.generation:
            tex.write(payload.tobytes())
            self._texture_generations[layer_id] = media.generation
        return tex

    def prune(self, live_ids: set[str]) -> None:
        stale = [lid for lid in self._textures if lid not in live_ids]
        for lid in stale:
            self._textures[lid].release()
            del self._textures[lid]
            self._texture_sizes.pop(lid, None)
            self._texture_generations.pop(lid, None)

    def view_params(
        self, model: MixerModel, viewport_width: int, viewport_height: int
    ) -> tuple[float, float, float]:
        canvas_w = float(max(1, model.canvas_width))
        canvas_h = float(max(1, model.canvas_height))
        scale = min(viewport_width / canvas_w, viewport_height / canvas_h)
        view_w = canvas_w * scale
        view_h = canvas_h * scale
        offset_x = (viewport_width - view_w) / 2.0
        offset_y = (viewport_height - view_h) / 2.0
        return scale, offset_x, offset_y

    def render(
        self,
        model: MixerModel,
        media_store: MediaStore,
        viewport_width: int,
        viewport_height: int,
        *,
        scene_id: str | None = None,
        clear_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    ) -> None:
        if self.ctx is None or self.prog is None or self.quad_vao is None:
            return

        if scene_id is None:
            scene = model.preview_scene()
        else:
            scene = model.scene_by_id(scene_id)

        vw = max(1, viewport_width)
        vh = max(1, viewport_height)
        self.ctx.viewport = (0, 0, vw, vh)
        self.ctx.clear(*clear_color)
        self.ctx.enable(self.ctx.BLEND)
        self.ctx.blend_func = self.ctx.SRC_ALPHA, self.ctx.ONE_MINUS_SRC_ALPHA
        # Disable depth — 2D compositor
        self.ctx.disable(self.ctx.DEPTH_TEST)

        if scene is None:
            return

        scale, offset_x, offset_y = self.view_params(model, vw, vh)
        proj = _ortho(0.0, float(vw), float(vh), 0.0)
        view = _mul(_translate(offset_x, offset_y), _scale(scale, scale))

        live_ids: set[str] = set()
        for layer in scene.layers:
            live_ids.add(layer.id)
            if not layer.visible or layer.file_missing:
                continue
            media = media_store.get(layer.id)
            if media is None or media.frame is None or media.width <= 0 or media.height <= 0:
                continue
            tex = self._upload(layer.id, media)
            if tex is None:
                continue

            model_m = layer_model_matrix(layer.transform, media.width, media.height)
            mvp = _mul(proj, view, model_m)
            tex.use(location=0)
            self.prog["u_tex"] = 0
            self.prog["u_opaque"] = 1.0 if media.kind == "image" else 0.0
            self.prog["u_mvp"].write(mvp.astype("f4").T.tobytes())
            self.quad_vao.render(mode=self.ctx.TRIANGLE_STRIP)

        self.prune(live_ids)

    def canvas_to_widget(
        self,
        model: MixerModel,
        viewport_width: int,
        viewport_height: int,
        canvas_x: float,
        canvas_y: float,
    ) -> tuple[float, float]:
        scale, offset_x, offset_y = self.view_params(model, viewport_width, viewport_height)
        return offset_x + canvas_x * scale, offset_y + canvas_y * scale

    def widget_to_canvas(
        self,
        model: MixerModel,
        viewport_width: int,
        viewport_height: int,
        widget_x: float,
        widget_y: float,
    ) -> tuple[float, float]:
        scale, offset_x, offset_y = self.view_params(model, viewport_width, viewport_height)
        if scale <= 0:
            return 0.0, 0.0
        return (widget_x - offset_x) / scale, (widget_y - offset_y) / scale
