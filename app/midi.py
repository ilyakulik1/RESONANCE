"""MIDI input hub: listen on all ports and bind CCs (MIDI Learn)."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

try:
    import rtmidi
except ImportError:
    rtmidi = None  # type: ignore[assignment]

VIRTUAL_PORT_NAME = "RESONANCE"
TARGET_MASTER_VOLUME = "master_volume"
_LEARN_TIMEOUT_MS = 12000
_SCAN_MS = 2000


@dataclass(frozen=True)
class MidiCcBinding:
    channel: int  # 0–15
    cc: int  # 0–127

    def to_dict(self) -> dict:
        return {"channel": int(self.channel), "cc": int(self.cc)}

    @classmethod
    def from_dict(cls, data: object) -> MidiCcBinding | None:
        if not isinstance(data, dict):
            return None
        try:
            channel = int(data.get("channel"))
            cc = int(data.get("cc"))
        except (TypeError, ValueError):
            return None
        if not (0 <= channel <= 15 and 0 <= cc <= 127):
            return None
        return cls(channel=channel, cc=cc)

    def label(self) -> str:
        return f"CC {self.cc} · ch {self.channel + 1}"


def midi_available() -> bool:
    return rtmidi is not None


class MidiHub(QObject):
    """Opens every MIDI input (plus a virtual port) and routes Control Change."""

    ccReceived = pyqtSignal(int, int, int)  # channel, cc, value 0–127
    learnChanged = pyqtSignal(str)  # target id, or "" when idle
    bindingChanged = pyqtSignal(str)  # target id

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._bindings: dict[str, MidiCcBinding] = {}
        self._learn_target: str = ""
        self._ports: dict[str, object] = {}
        self._virtual = None
        self._alive = False
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(_SCAN_MS)
        self._scan_timer.timeout.connect(self._scan_ports)
        self._learn_timer = QTimer(self)
        self._learn_timer.setSingleShot(True)
        self._learn_timer.timeout.connect(self.cancel_learn)
        self.ccReceived.connect(self.handle_cc)

    def start(self) -> None:
        if self._alive:
            return
        self._alive = True
        self._open_virtual()
        self._scan_ports()
        self._scan_timer.start()

    def shutdown(self) -> None:
        self._alive = False
        self._scan_timer.stop()
        self._learn_timer.stop()
        self._learn_target = ""
        for port in list(self._ports.values()):
            self._close_port(port)
        self._ports.clear()
        if self._virtual is not None:
            self._close_port(self._virtual)
            self._virtual = None

    def binding_for(self, target: str) -> MidiCcBinding | None:
        return self._bindings.get(target)

    @property
    def learn_target(self) -> str:
        return self._learn_target

    def refresh(self) -> None:
        self._scan_ports()

    def bindings_to_dict(self) -> dict[str, dict]:
        return {key: binding.to_dict() for key, binding in self._bindings.items()}

    def set_bindings(self, data: object) -> None:
        self._bindings.clear()
        if not isinstance(data, dict):
            return
        for key, raw in data.items():
            binding = MidiCcBinding.from_dict(raw)
            if binding is not None:
                self._bindings[str(key)] = binding

    def start_learn(self, target: str) -> bool:
        if not midi_available():
            return False
        if not self._alive:
            self.start()
        self._learn_target = target
        self._learn_timer.start(_LEARN_TIMEOUT_MS)
        self.learnChanged.emit(target)
        return True

    def cancel_learn(self) -> None:
        if not self._learn_target:
            return
        self._learn_target = ""
        self._learn_timer.stop()
        self.learnChanged.emit("")

    def clear_binding(self, target: str) -> None:
        self.cancel_learn()
        if target in self._bindings:
            del self._bindings[target]
            self.bindingChanged.emit(target)

    def _open_virtual(self) -> None:
        if not midi_available() or self._virtual is not None:
            return
        try:
            port = rtmidi.MidiIn()
            port.ignore_types(sysex=True, timing=True, active_sense=True)
            port.open_virtual_port(VIRTUAL_PORT_NAME)
            port.set_callback(self._on_rtmidi)
            self._virtual = port
        except Exception:
            self._virtual = None

    def _scan_ports(self) -> None:
        if not midi_available() or not self._alive:
            return
        names = self._list_port_names()
        wanted = {name for name in names if name and VIRTUAL_PORT_NAME not in name}
        for name in list(self._ports):
            if name not in wanted:
                self._close_port(self._ports.pop(name))
        for name in wanted:
            if name not in self._ports:
                opened = self._open_named_port(name)
                if opened is not None:
                    self._ports[name] = opened

    def _list_port_names(self) -> list[str]:
        probe = None
        try:
            probe = rtmidi.MidiIn()
            return [probe.get_port_name(i) for i in range(probe.get_port_count())]
        except Exception:
            return []
        finally:
            if probe is not None:
                self._close_port(probe)

    def _open_named_port(self, name: str):
        probe = None
        try:
            probe = rtmidi.MidiIn()
            index = None
            for i in range(probe.get_port_count()):
                if probe.get_port_name(i) == name:
                    index = i
                    break
            if index is None:
                return None
            probe.ignore_types(sysex=True, timing=True, active_sense=True)
            probe.open_port(index)
            probe.set_callback(self._on_rtmidi)
            opened, probe = probe, None
            return opened
        except Exception:
            return None
        finally:
            if probe is not None:
                self._close_port(probe)

    def _on_rtmidi(self, event, _data=None) -> None:
        if not self._alive:
            return
        try:
            message, _delta = event
        except (TypeError, ValueError):
            return
        if not message or len(message) < 3:
            return
        status, cc, value = int(message[0]), int(message[1]), int(message[2])
        if status & 0xF0 != 0xB0:
            return
        self.ccReceived.emit(status & 0x0F, cc & 0x7F, value & 0x7F)

    def handle_cc(self, channel: int, cc: int, value: int) -> None:
        """Main-thread: learn or dispatch. Connected from ccReceived."""
        if self._learn_target:
            target = self._learn_target
            self._bindings[target] = MidiCcBinding(channel=channel, cc=cc)
            self._learn_target = ""
            self._learn_timer.stop()
            self.learnChanged.emit("")
            self.bindingChanged.emit(target)

    @staticmethod
    def _close_port(port) -> None:
        try:
            port.cancel_callback()
        except Exception:
            pass
        try:
            port.close_port()
        except Exception:
            pass
        try:
            del port
        except Exception:
            pass
