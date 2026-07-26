"""Parametric EQ state: bands with freq / gain / Q / type."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

BAND_TYPES = ("bell", "low_shelf", "high_shelf", "low_cut", "high_cut")
BAND_TYPE_LABELS = {
    "bell": "Bell",
    "low_shelf": "Low Shelf",
    "high_shelf": "High Shelf",
    "low_cut": "Low Cut",
    "high_cut": "High Cut",
}

FREQ_MIN_HZ = 20.0
FREQ_MAX_HZ = 20000.0
GAIN_MIN_DB = -18.0
GAIN_MAX_DB = 18.0
Q_MIN = 0.1
Q_MAX = 100.0
DEFAULT_Q = 1.0
MAX_BANDS = 24


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class EqBand:
    id: str = field(default_factory=_new_id)
    freq_hz: float = 1000.0
    gain_db: float = 0.0
    q: float = DEFAULT_Q
    type: str = "bell"
    enabled: bool = True

    def __post_init__(self) -> None:
        self.normalize()

    def normalize(self) -> None:
        self.freq_hz = _clamp(self.freq_hz, FREQ_MIN_HZ, FREQ_MAX_HZ)
        self.gain_db = _clamp(self.gain_db, GAIN_MIN_DB, GAIN_MAX_DB)
        self.q = _clamp(self.q, Q_MIN, Q_MAX)
        if self.type not in BAND_TYPES:
            self.type = "bell"
        self.enabled = bool(self.enabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "freq_hz": round(self.freq_hz, 3),
            "gain_db": round(self.gain_db, 3),
            "q": round(self.q, 4),
            "type": self.type,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EqBand | None:
        if not isinstance(data, dict):
            return None
        return cls(
            id=str(data.get("id") or _new_id()),
            freq_hz=float(data.get("freq_hz", 1000.0)),
            gain_db=float(data.get("gain_db", 0.0)),
            q=float(data.get("q", DEFAULT_Q)),
            type=str(data.get("type", "bell")),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class EqState:
    bands: list[EqBand] = field(default_factory=list)
    bypass: bool = False
    selected_id: str | None = None

    def band_by_id(self, band_id: str | None) -> EqBand | None:
        if not band_id:
            return None
        for band in self.bands:
            if band.id == band_id:
                return band
        return None

    def selected_band(self) -> EqBand | None:
        return self.band_by_id(self.selected_id)

    def select(self, band_id: str | None) -> None:
        if band_id and self.band_by_id(band_id):
            self.selected_id = band_id
        else:
            self.selected_id = None

    def add_band(
        self,
        *,
        freq_hz: float = 1000.0,
        gain_db: float = 0.0,
        q: float = DEFAULT_Q,
        type: str = "bell",
    ) -> EqBand | None:
        if len(self.bands) >= MAX_BANDS:
            return None
        band = EqBand(freq_hz=freq_hz, gain_db=gain_db, q=q, type=type)
        self.bands.append(band)
        self.selected_id = band.id
        return band

    def remove_band(self, band_id: str) -> bool:
        before = len(self.bands)
        self.bands = [b for b in self.bands if b.id != band_id]
        if self.selected_id == band_id:
            self.selected_id = self.bands[-1].id if self.bands else None
        return len(self.bands) < before

    def clear_bands(self) -> None:
        self.bands.clear()
        self.selected_id = None

    def reset(self) -> None:
        self.clear_bands()
        self.bypass = False

    def enabled_bands(self) -> Iterable[EqBand]:
        if self.bypass:
            return ()
        return (b for b in self.bands if b.enabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bypass": self.bypass,
            "selected_id": self.selected_id,
            "bands": [b.to_dict() for b in self.bands],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EqState:
        state = cls()
        if not isinstance(data, dict):
            return state
        state.bypass = bool(data.get("bypass", False))
        bands_raw = data.get("bands")
        if isinstance(bands_raw, list):
            for entry in bands_raw:
                band = EqBand.from_dict(entry)
                if band is not None and len(state.bands) < MAX_BANDS:
                    state.bands.append(band)
        selected = data.get("selected_id")
        if isinstance(selected, str) and state.band_by_id(selected):
            state.selected_id = selected
        elif state.bands:
            state.selected_id = state.bands[0].id
        return state
