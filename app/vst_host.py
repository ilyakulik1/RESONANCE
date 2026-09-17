"""Master-output VST3/AU plugin chain (pedalboard).

Each air deck gets its own plugin instances so compressor/limiter state is not
shared across a crossfade. The chain still behaves as a master insert: it runs
after per-track EQ/stems/gain and before Int16 conversion.
"""

from __future__ import annotations

import base64
import os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

MAX_SLOTS = 8
_PLUGIN_SUFFIXES = (".vst3", ".component", ".vst")


def pedalboard_available() -> bool:
    try:
        import pedalboard  # noqa: F401
    except Exception:
        return False
    return True


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def plugin_search_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        candidates = [
            home / "Library/Audio/Plug-Ins/VST3",
            Path("/Library/Audio/Plug-Ins/VST3"),
            home / "Library/Audio/Plug-Ins/Components",
            Path("/Library/Audio/Plug-Ins/Components"),
            Path("/System/Library/Audio/Plug-Ins/Components"),
        ]
    elif sys.platform == "win32":
        common = Path(os.environ.get("COMMONPROGRAMFILES", r"C:\Program Files\Common Files"))
        program = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates = [
            common / "VST3",
            program / "Common Files" / "VST3",
            program / "Steinberg" / "VSTPlugins",
        ]
    else:
        candidates = [
            home / ".vst3",
            Path("/usr/lib/vst3"),
            Path("/usr/local/lib/vst3"),
            Path("/usr/lib/x86_64-linux-gnu/vst3"),
        ]
    return [path for path in candidates if path.is_dir()]


def scan_installed_plugins() -> list[tuple[str, str]]:
    """Return (display_name, path) for VST3/AU bundles in standard folders."""
    found: dict[str, tuple[str, str, int]] = {}
    rank = {".vst3": 0, ".vst": 1, ".component": 2}

    def collect(folder: Path) -> None:
        try:
            children = list(folder.iterdir())
        except OSError:
            return
        for item in children:
            suffix = item.suffix.lower()
            if suffix in _PLUGIN_SUFFIXES:
                if suffix == ".component" and (
                    item.stem.startswith("Apple") or item.stem.endswith("AUHook")
                ):
                    continue
                key = item.stem.lower()
                this_rank = rank.get(suffix, 9)
                prev = found.get(key)
                if prev is None or this_rank < prev[2]:
                    try:
                        resolved = str(item.resolve())
                    except OSError:
                        resolved = str(item)
                    found[key] = (item.stem, resolved, this_rank)
                continue
            if item.is_dir() and not item.name.startswith("."):
                collect(item)

    for root in plugin_search_dirs():
        collect(root)
    result = [(name, path) for name, path, _ in found.values()]
    result.sort(key=lambda row: row[0].lower())
    return result


def _plugin_display_name(plugin: Any, fallback: str) -> str:
    for attr in ("name", "descriptive_name"):
        value = getattr(plugin, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _snapshot_parameters(plugin: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    try:
        params = plugin.parameters
    except Exception:
        return values
    if not params:
        return values
    try:
        items = params.items()
    except Exception:
        return values
    for name, param in items:
        try:
            values[str(name)] = param.raw_value
        except Exception:
            try:
                values[str(name)] = getattr(plugin, name)
            except Exception:
                continue
    return values


def _apply_parameters(plugin: Any, values: dict[str, Any] | None) -> None:
    if not values:
        return
    for name, value in values.items():
        try:
            setattr(plugin, name, value)
        except Exception:
            continue


def _encode_raw_state(plugin: Any) -> str | None:
    try:
        raw = plugin.raw_state
    except Exception:
        return None
    if raw is None:
        return None
    try:
        data = bytes(raw)
    except Exception:
        return None
    if not data:
        return None
    return base64.b64encode(data).decode("ascii")


def _apply_raw_state(plugin: Any, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        plugin.raw_state = base64.b64decode(encoded)
        return True
    except Exception:
        return False


def _load_effect(path: str, *, plugin_name: str | None = None):
    from pedalboard import load_plugin

    kwargs: dict[str, Any] = {}
    if plugin_name:
        kwargs["plugin_name"] = plugin_name
    plugin = load_plugin(path, **kwargs)
    if getattr(plugin, "is_instrument", False) and not getattr(plugin, "is_effect", True):
        name = _plugin_display_name(plugin, Path(path).stem)
        raise ValueError(f"{name} is an instrument, not an effect")
    return plugin


@dataclass
class PluginSlot:
    id: str = field(default_factory=_new_id)
    path: str = ""
    name: str = ""
    plugin_name: str | None = None
    enabled: bool = True
    raw_state: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "enabled": self.enabled,
        }
        if self.plugin_name:
            payload["plugin_name"] = self.plugin_name
        if self.raw_state:
            payload["raw_state"] = self.raw_state
        if self.parameters:
            payload["parameters"] = dict(self.parameters)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PluginSlot | None:
        if not isinstance(data, dict):
            return None
        path = str(data.get("path") or "").strip()
        if not path:
            return None
        params = data.get("parameters")
        return cls(
            id=str(data.get("id") or _new_id()),
            path=path,
            name=str(data.get("name") or Path(path).stem),
            plugin_name=str(data["plugin_name"]) if data.get("plugin_name") else None,
            enabled=bool(data.get("enabled", True)),
            raw_state=str(data["raw_state"]) if data.get("raw_state") else None,
            parameters=dict(params) if isinstance(params, dict) else {},
        )


class _ChainCopy:
    def __init__(self) -> None:
        self.plugins: list[Any | None] = []


class MasterFxHost(QObject):
    """Realtime VST/AU chain with one live copy per air deck."""

    slotsChanged = pyqtSignal()
    changed = pyqtSignal()
    loadFailed = pyqtSignal(str)

    def __init__(self, *, copy_count: int = 2, parent=None):
        super().__init__(parent)
        self._copy_count = max(1, int(copy_count))
        self._slots: list[PluginSlot] = []
        self._copies = [_ChainCopy() for _ in range(self._copy_count)]
        self._ui = _ChainCopy()
        self._bypass = False
        self._active = False
        self._sample_rate = 44100.0
        self._lock = threading.Lock()

    @property
    def bypass(self) -> bool:
        return self._bypass

    def is_available(self) -> bool:
        return pedalboard_available()

    def is_active(self) -> bool:
        return self._active

    def slots(self) -> list[PluginSlot]:
        with self._lock:
            return list(self._slots)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "bypass": self._bypass,
                "slots": [slot.to_dict() for slot in self._slots],
            }

    def load_dict(self, data: dict[str, Any] | None) -> None:
        slots: list[PluginSlot] = []
        bypass = False
        if isinstance(data, dict):
            bypass = bool(data.get("bypass", False))
            raw_slots = data.get("slots")
            if isinstance(raw_slots, list):
                for entry in raw_slots:
                    slot = PluginSlot.from_dict(entry)
                    if slot is not None and len(slots) < MAX_SLOTS:
                        slots.append(slot)
        with self._lock:
            self._slots = slots
            self._bypass = bypass
            self._rebuild_copies_locked()
            self._refresh_active_locked()
        self.slotsChanged.emit()

    def set_bypass(self, bypass: bool) -> None:
        flag = bool(bypass)
        with self._lock:
            if flag == self._bypass:
                return
            self._bypass = flag
            self._refresh_active_locked()
        self.slotsChanged.emit()
        self.changed.emit()

    def set_sample_rate(self, sample_rate: float) -> None:
        self._sample_rate = float(sample_rate)

    def reset_copy(self, copy_index: int) -> None:
        copy = self._copy(copy_index)
        if copy is None:
            return
        with self._lock:
            for plugin in copy.plugins:
                if plugin is None:
                    continue
                try:
                    plugin.reset()
                except Exception:
                    pass

    def add_plugin(self, path: str, *, plugin_name: str | None = None) -> None:
        if not self.is_available():
            self.loadFailed.emit("pedalboard is not installed")
            return
        if len(self._slots) >= MAX_SLOTS:
            self.loadFailed.emit(f"Master FX is limited to {MAX_SLOTS} plugins")
            return
        resolved = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(resolved):
            self.loadFailed.emit(f"Plugin not found:\n{resolved}")
            return
        try:
            plugin = _load_effect(resolved, plugin_name=plugin_name)
        except Exception as exc:
            self.loadFailed.emit(str(exc) or "Could not load plugin")
            return
        slot = PluginSlot(
            path=resolved,
            name=_plugin_display_name(plugin, Path(resolved).stem),
            plugin_name=plugin_name,
            parameters=_snapshot_parameters(plugin),
            raw_state=_encode_raw_state(plugin),
        )
        ui_plugin = plugin
        audio_instances: list[Any | None] = []
        try:
            for _ in range(self._copy_count):
                audio_instances.append(self._instantiate_slot(slot))
        except Exception as exc:
            self.loadFailed.emit(str(exc) or "Could not load plugin")
            return
        over_limit = False
        with self._lock:
            if len(self._slots) >= MAX_SLOTS:
                over_limit = True
            else:
                self._slots.append(slot)
                self._append_plugin_set_locked(ui_plugin, audio_instances)
                self._refresh_active_locked()
        if over_limit:
            self.loadFailed.emit(f"Master FX is limited to {MAX_SLOTS} plugins")
            return
        self.slotsChanged.emit()
        self.changed.emit()

    def remove_slot(self, slot_id: str) -> None:
        with self._lock:
            index = self._index_of(slot_id)
            if index is None:
                return
            del self._slots[index]
            if 0 <= index < len(self._ui.plugins):
                self._ui.plugins.pop(index)
            for copy in self._copies:
                if 0 <= index < len(copy.plugins):
                    copy.plugins.pop(index)
            self._refresh_active_locked()
        self.slotsChanged.emit()
        self.changed.emit()

    def move_slot(self, slot_id: str, delta: int) -> None:
        with self._lock:
            index = self._index_of(slot_id)
            if index is None:
                return
            target = index + int(delta)
            if target < 0 or target >= len(self._slots):
                return
            self._slots[index], self._slots[target] = self._slots[target], self._slots[index]
            ui_plugins = self._ui.plugins
            if index < len(ui_plugins) and target < len(ui_plugins):
                ui_plugins[index], ui_plugins[target] = ui_plugins[target], ui_plugins[index]
            for copy in self._copies:
                plugins = copy.plugins
                if index < len(plugins) and target < len(plugins):
                    plugins[index], plugins[target] = plugins[target], plugins[index]
        self.slotsChanged.emit()
        self.changed.emit()

    def set_slot_enabled(self, slot_id: str, enabled: bool) -> None:
        with self._lock:
            slot = self._slot(slot_id)
            if slot is None or slot.enabled == bool(enabled):
                return
            slot.enabled = bool(enabled)
            self._refresh_active_locked()
        self.slotsChanged.emit()
        self.changed.emit()

    def show_editor(self, slot_id: str, *, copy_index: int = 0) -> None:
        """Open the native plugin UI on a dedicated instance, not the air DSP.

        Pedalboard requires the main thread and blocks until the window closes.
        """
        del copy_index
        plugin = None
        slot_index = None
        with self._lock:
            slot_index = self._index_of(slot_id)
            if slot_index is not None and slot_index < len(self._ui.plugins):
                plugin = self._ui.plugins[slot_index]
        if plugin is None or slot_index is None:
            self.loadFailed.emit("Plugin is not loaded")
            return
        if not hasattr(plugin, "show_editor"):
            self.loadFailed.emit("This plugin has no editor")
            return
        try:
            plugin.show_editor()
        except Exception as exc:
            self.loadFailed.emit(str(exc) or "Could not open plugin editor")
            return
        encoded = _encode_raw_state(plugin)
        params = _snapshot_parameters(plugin)
        snapshot = None
        with self._lock:
            if slot_index >= len(self._slots):
                return
            slot = self._slots[slot_index]
            if encoded:
                slot.raw_state = encoded
            if params:
                slot.parameters = params
            snapshot = PluginSlot.from_dict(slot.to_dict())
        if snapshot is not None:
            self._swap_audio_slot_async(slot_index, snapshot)
        self.changed.emit()

    def process_interleaved(
        self,
        copy_index: int,
        interleaved: np.ndarray,
        sample_rate: float,
    ) -> np.ndarray:
        if not self._active or interleaved.size < 2:
            return interleaved
        copy = self._copy(copy_index)
        if copy is None:
            return interleaved
        frames = interleaved.size // 2
        if frames <= 0:
            return interleaved
        rate = float(sample_rate) if sample_rate > 0 else self._sample_rate
        with self._lock:
            plugins = [
                plugin
                for index, plugin in enumerate(copy.plugins)
                if plugin is not None
                and index < len(self._slots)
                and self._slots[index].enabled
            ]
        planar = np.ascontiguousarray(
            interleaved[: frames * 2].reshape(frames, 2).T, dtype=np.float32
        )
        for plugin in plugins:
            try:
                out = plugin.process(planar, rate, reset=False)
            except Exception:
                continue
            planar = _match_planar(out, frames)
        return np.ascontiguousarray(planar.T, dtype=np.float32).reshape(-1)

    def _copy(self, copy_index: int) -> _ChainCopy | None:
        if 0 <= copy_index < len(self._copies):
            return self._copies[copy_index]
        return None

    def _slot(self, slot_id: str) -> PluginSlot | None:
        for slot in self._slots:
            if slot.id == slot_id:
                return slot
        return None

    def _index_of(self, slot_id: str) -> int | None:
        for index, slot in enumerate(self._slots):
            if slot.id == slot_id:
                return index
        return None

    def _instantiate_slot(self, slot: PluginSlot):
        plugin = _load_effect(slot.path, plugin_name=slot.plugin_name)
        if not _apply_raw_state(plugin, slot.raw_state):
            _apply_parameters(plugin, slot.parameters)
        return plugin

    def _append_plugin_set_locked(
        self, ui_plugin: Any | None, audio_plugins: list[Any | None]
    ) -> None:
        self._ui.plugins.append(ui_plugin)
        while len(audio_plugins) < self._copy_count:
            audio_plugins.append(None)
        for copy, plugin in zip(self._copies, audio_plugins):
            copy.plugins.append(plugin)

    def _rebuild_copies_locked(self) -> None:
        ui_row: list[Any | None] = []
        rebuilt: list[list[Any | None]] = [[] for _ in range(self._copy_count)]
        available = pedalboard_available()
        for slot in self._slots:
            slot.error = None
            if not available:
                slot.error = "pedalboard is not installed"
                ui_row.append(None)
                for row in rebuilt:
                    row.append(None)
                continue
            if not os.path.exists(slot.path):
                slot.error = "Missing plugin file"
                ui_row.append(None)
                for row in rebuilt:
                    row.append(None)
                continue
            try:
                ui_plugin = self._instantiate_slot(slot)
            except Exception as exc:
                slot.error = str(exc) or "Load failed"
                ui_row.append(None)
                for row in rebuilt:
                    row.append(None)
                continue
            if not slot.name:
                slot.name = _plugin_display_name(ui_plugin, Path(slot.path).stem)
            ui_row.append(ui_plugin)
            for copy_index in range(self._copy_count):
                try:
                    rebuilt[copy_index].append(self._instantiate_slot(slot))
                except Exception:
                    rebuilt[copy_index].append(None)
        self._ui.plugins = ui_row
        for copy, plugins in zip(self._copies, rebuilt):
            copy.plugins = plugins

    def _refresh_active_locked(self) -> None:
        self._active = (not self._bypass) and any(
            slot.enabled and not slot.error for slot in self._slots
        )

    def _swap_audio_slot_async(self, slot_index: int, snapshot: PluginSlot) -> None:
        slot_id = snapshot.id

        def work() -> None:
            prepared: list[Any | None] = []
            try:
                for _ in range(self._copy_count):
                    prepared.append(self._instantiate_slot(snapshot))
            except Exception:
                return
            with self._lock:
                if (
                    slot_index >= len(self._slots)
                    or self._slots[slot_index].id != slot_id
                ):
                    return
                for copy, plugin in zip(self._copies, prepared):
                    if slot_index < len(copy.plugins):
                        copy.plugins[slot_index] = plugin

        threading.Thread(target=work, name="VstAudioSwap", daemon=True).start()


def _match_planar(out: np.ndarray | None, frames: int) -> np.ndarray:
    if out is None:
        return np.zeros((2, frames), dtype=np.float32)
    data = np.asarray(out, dtype=np.float32)
    if data.ndim == 1:
        data = np.vstack([data, data])
    elif data.shape[0] == 1:
        data = np.vstack([data[0], data[0]])
    else:
        data = data[:2]
    got = int(data.shape[1])
    if got == frames:
        return np.ascontiguousarray(data, dtype=np.float32)
    aligned = np.zeros((2, frames), dtype=np.float32)
    take = min(got, frames)
    if take > 0:
        aligned[:, :take] = data[:, :take]
    return aligned
