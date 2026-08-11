from __future__ import annotations

import base64

from PyQt6.QtMultimedia import QAudioDevice, QMediaDevices
from PyQt6.QtWidgets import QComboBox

# Sentinel stored in combo item data / config for silent routing.
NO_OUTPUT_DEVICE_ID = b"__no_output__"
NO_OUTPUT_CONFIG_VALUE = "no_output"
NO_OUTPUT_LABEL = "NO OUTPUT"


def is_no_output_device(device_id: bytes | None) -> bool:
    return device_id == NO_OUTPUT_DEVICE_ID


def device_id_to_config(device_id: bytes) -> str:
    if device_id == NO_OUTPUT_DEVICE_ID:
        return NO_OUTPUT_CONFIG_VALUE
    return base64.standard_b64encode(device_id).decode("ascii")


def device_id_from_config(value: str) -> bytes | None:
    if not value:
        return None
    if value == NO_OUTPUT_CONFIG_VALUE:
        return NO_OUTPUT_DEVICE_ID
    try:
        return base64.standard_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


def find_audio_output(device_id: bytes | None) -> QAudioDevice:
    devices = QMediaDevices.audioOutputs()
    if device_id and not is_no_output_device(device_id):
        for device in devices:
            if device.id().data() == device_id:
                return device
    return QMediaDevices.defaultAudioOutput()


def populate_audio_output_combo(combo: QComboBox, *, saved_device_id: bytes | None = None) -> None:
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(NO_OUTPUT_LABEL, NO_OUTPUT_DEVICE_ID)

    devices = QMediaDevices.audioOutputs()
    default_device = QMediaDevices.defaultAudioOutput()
    selected_index = 1 if devices else 0

    if is_no_output_device(saved_device_id):
        selected_index = 0

    for index, device in enumerate(devices):
        label = device.description()
        if device.isDefault():
            label = f"{label} (Default)"
        combo.addItem(label, device.id().data())
        item_index = index + 1
        if saved_device_id and device.id().data() == saved_device_id:
            selected_index = item_index
        elif saved_device_id is None and device == default_device:
            selected_index = item_index

    combo.setCurrentIndex(selected_index)
    combo.blockSignals(False)


def refresh_audio_output_combos(*combos: QComboBox) -> None:
    """Re-enumerate system outputs, preserving each combo's current selection."""
    for combo in combos:
        if combo is None:
            continue
        saved = combo_selected_device_id(combo)
        populate_audio_output_combo(combo, saved_device_id=saved)


def combo_selected_device_id(combo: QComboBox) -> bytes | None:
    data = combo.currentData()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return None
