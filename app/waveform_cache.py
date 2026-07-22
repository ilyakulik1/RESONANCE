from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class WaveformCacheKey:
    path: str
    num_bars: int
    start_ms: int
    end_ms: int

    @classmethod
    def create(
        cls,
        file_path: str,
        num_bars: int,
        *,
        start_ms: int = 0,
        end_ms: int = 0,
    ) -> WaveformCacheKey:
        return cls(
            path=os.path.abspath(file_path),
            num_bars=num_bars,
            start_ms=max(0, start_ms),
            end_ms=max(0, end_ms),
        )


@dataclass
class WaveformCacheEntry:
    data: list[float]
    map_start_ms: int = 0
    map_end_ms: int = 0


class WaveformCache:
    """In-memory cache of generated waveform peaks keyed by file and resolution."""

    def __init__(self, max_entries: int = 128):
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[WaveformCacheKey, WaveformCacheEntry] = OrderedDict()

    def get(self, key: WaveformCacheKey) -> WaveformCacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry

    def put(self, key: WaveformCacheKey, entry: WaveformCacheEntry) -> None:
        if key in self._entries:
            del self._entries[key]
        self._entries[key] = entry
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
