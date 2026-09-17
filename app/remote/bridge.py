from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer

from app.constants import FADE_PRESETS_MS, fade_preset_label
from app.eq_dsp import HIGH_CUT_Q_DEFAULT, HIGH_CUT_Q_MAX, HIGH_CUT_Q_MIN
from app.playlist_io import (
    get_item_bpm,
    get_item_color,
    get_item_duration,
    get_item_file_path,
    get_item_track_id,
    is_item_file_missing,
)

if TYPE_CHECKING:
    from app.player import AudioPlayer
    from app.widgets.playlist_widget import PlaylistWidget


class RemoteBridge(QObject):
    """GUI-thread snapshot + command dispatch for the LAN remote."""

    statePublished = pyqtSignal(str)
    _invoke = pyqtSignal(object)

    def __init__(self, player: "AudioPlayer"):
        super().__init__(player)
        self._player = player
        self._lock = threading.Lock()
        self._json = "{}"
        self._seq = 0
        self._invoke.connect(self._run_invoked, Qt.ConnectionType.QueuedConnection)

    def _run_invoked(self, fn) -> None:
        fn()

    def cached_json(self) -> str:
        with self._lock:
            return self._json

    def refresh(self) -> str:
        try:
            payload = self._build_snapshot()
        except Exception as exc:
            print(f"Remote snapshot error: {exc}")
            return self.cached_json()
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._json = text
            self._seq += 1
        self.statePublished.emit(text)
        return text

    def run_command(self, action: str, params: dict | None, timeout: float = 8.0) -> dict:
        params = params or {}
        box: dict[str, Any] = {}
        done = threading.Event()

        def _run() -> None:
            try:
                box["result"] = self._dispatch(action, params)
                self.refresh()
            except Exception as exc:
                box["result"] = {"ok": False, "error": str(exc)}
            finally:
                done.set()

        self._invoke.emit(_run)
        if not done.wait(timeout):
            return {"ok": False, "error": "Player is busy"}
        result = box.get("result")
        if isinstance(result, dict):
            return result
        return {"ok": True}

    def _dispatch(self, action: str, params: dict) -> dict:
        player = self._player
        handlers = {
            "on_air": lambda: player.handle_space(),
            "stop": lambda: player.stop(),
            "play_pause": lambda: player.toggle_play(),
            "pause": lambda: player.pause(),
            "next": lambda: player.next_track(),
            "previous": lambda: player.previous_track(),
            "rewind": lambda: player.rewind_to_start(),
            "advance": lambda: player.advance_selection(),
            "seek": lambda: player.seek(int(params.get("position_ms", 0))),
            "volume": lambda: self._set_volume(params),
            "fade_mode": lambda: player.on_fade_mode_changed(str(params.get("mode") or "")),
            "fade_take": lambda: player.trigger_fade_mode_take(str(params.get("mode") or "")),
            "fade_duration": lambda: player.apply_fade_preset(int(params.get("ms", 0))),
            "fade_preset_step": lambda: player.step_fade_preset(int(params.get("direction", 1))),
            "playback_mode": lambda: self._set_playback_mode(str(params.get("mode") or "")),
            "advance_enabled": lambda: self._set_advance(bool(params.get("enabled"))),
            "high_cut_q": lambda: self._set_high_cut_q(params.get("q")),
            "select_track": lambda: self._select_track(params, take=False),
            "play_track": lambda: self._select_track(params, take=True),
            "move_track": lambda: self._move_track(params),
            "remove_track": lambda: self._remove_track(params),
            "activate_tab": lambda: self._activate_tab(params),
            "rename_playlist": lambda: self._rename_playlist(params),
            "add_playlist": lambda: self._add_playlist(params),
            "delete_playlist": lambda: self._delete_playlist(params),
        }
        handler = handlers.get(action)
        if handler is None:
            return {"ok": False, "error": f"Unknown action: {action}"}
        handler()
        return {"ok": True}

    def _set_volume(self, params: dict) -> None:
        volume = max(0.0, min(1.0, float(params.get("volume", 0))))
        panel = getattr(self._player, "file_properties_panel", None)
        if panel is not None:
            panel.set_master_volume(volume)
        self._player._on_master_volume_changed(volume)

    def _set_playback_mode(self, mode: str) -> None:
        if mode not in ("loop", "next", "stop"):
            return
        self._player.set_playback_mode(mode)

    def _set_advance(self, enabled: bool) -> None:
        box = getattr(self._player, "space_advances_checkbox", None)
        if box is not None:
            box.setChecked(bool(enabled))
        self._player.save_config()

    def _set_high_cut_q(self, raw) -> None:
        try:
            q = max(HIGH_CUT_Q_MIN, min(HIGH_CUT_Q_MAX, float(raw)))
        except (TypeError, ValueError):
            return
        stepper = getattr(self._player, "high_cut_q_stepper", None)
        if stepper is not None:
            stepper.set_value(q)
        self._player.save_config()

    def _select_track(self, params: dict, *, take: bool) -> None:
        playlist, item = self._resolve_item(params)
        if playlist is None or item is None:
            return
        self._activate_playlist_widget(playlist)
        playlist.setCurrentItem(item)
        self._player.on_playlist_item_clicked(playlist.playlist_num, item)
        if take:
            self._player.handle_space()

    def _move_track(self, params: dict) -> None:
        playlist, item = self._resolve_item(params)
        if playlist is None or item is None:
            return
        playlist.setCurrentItem(item)
        where = str(params.get("where") or "down")
        if where == "up":
            playlist.move_item_up()
        elif where == "down":
            playlist.move_item_down()
        elif where == "top":
            playlist.move_item_top()
        elif where == "bottom":
            playlist.move_item_bottom()
        self._player.refresh_playlist_footer(playlist)

    def _remove_track(self, params: dict) -> None:
        playlist, item = self._resolve_item(params)
        if playlist is None or item is None:
            return
        row = playlist.row(item)
        if row < 0:
            return
        playlist.takeItem(row)
        self._player.refresh_playlist_footer(playlist)

    def _activate_tab(self, params: dict) -> None:
        playlist_num = params.get("playlist_num")
        if playlist_num is not None:
            playlist = self._player.get_playlist(int(playlist_num))
            if playlist is not None:
                self._activate_playlist_widget(playlist)
            return
        column_index = int(params.get("column", 0))
        tab_index = int(params.get("tab", 0))
        columns = getattr(self._player, "playlist_columns", None) or []
        if not (0 <= column_index < len(columns)):
            return
        column = columns[column_index]
        if 0 <= tab_index < column.tabs.count():
            column.tabs.setCurrentIndex(tab_index)

    def _rename_playlist(self, params: dict) -> None:
        playlist_num = int(params.get("playlist_num", 0))
        title = str(params.get("title") or "").strip()
        if playlist_num and title:
            self._player.on_playlist_title_changed(playlist_num, title)

    def _add_playlist(self, params: dict) -> None:
        column_index = int(params.get("column", 0))
        columns = getattr(self._player, "playlist_columns", None) or []
        if not (0 <= column_index < len(columns)):
            return
        title = str(params.get("title") or "").strip() or None
        columns[column_index].add_empty_tab(title=title)

    def _delete_playlist(self, params: dict) -> None:
        playlist_num = int(params.get("playlist_num", 0))
        playlist = self._player.get_playlist(playlist_num)
        if playlist is None:
            return
        column = self._player._column_for_playlist(playlist)
        if column is None:
            return
        for i in range(column.tabs.count()):
            page = column.tabs.widget(i)
            pl = getattr(page, "playlist_widget", None) if page else None
            if pl is playlist:
                column._delete_tab(i, confirm=False)
                return

    def _activate_playlist_widget(self, playlist: "PlaylistWidget") -> None:
        column = self._player._column_for_playlist(playlist)
        if column is None:
            return
        for i in range(column.tabs.count()):
            page = column.tabs.widget(i)
            pl = getattr(page, "playlist_widget", None) if page else None
            if pl is playlist:
                if column.tabs.currentIndex() != i:
                    column.tabs.setCurrentIndex(i)
                break

    def _resolve_item(self, params: dict):
        playlist_num = params.get("playlist_num")
        if playlist_num is None:
            return None, None
        playlist = self._player.get_playlist(int(playlist_num))
        if playlist is None:
            return None, None
        track_id = str(params.get("track_id") or "").strip()
        if track_id:
            for row in range(playlist.count()):
                item = playlist.item(row)
                if item is None:
                    continue
                if get_item_track_id(item) == track_id:
                    return playlist, item
        if "row" in params:
            try:
                row = int(params["row"])
            except (TypeError, ValueError):
                return playlist, None
            if 0 <= row < playlist.count():
                return playlist, playlist.item(row)
        return playlist, None

    def _build_snapshot(self) -> dict:
        player = self._player
        media = player.player
        state = media.playbackState()
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        paused = state == QMediaPlayer.PlaybackState.PausedState
        air_item = player.current_playing_item
        air_path = player._current_file_path
        preview_item = getattr(player, "_preview_item", None)
        selected_num, selected_item = player.get_selected_track()
        duration_ms = int(media.duration() or 0)
        range_end = player._range_end_ms() if duration_ms > 0 else 0
        return {
            "app": "RESONANCE",
            "air": {
                "playing": playing,
                "paused": paused,
                "name": player.get_track_display_name(air_item, air_path),
                "track_id": get_item_track_id(air_item) if air_item is not None else None,
                "playlist_num": player.current_playing_playlist,
                "position_ms": int(media.position() or 0),
                "duration_ms": duration_ms,
                "range_start_ms": player._range_start_ms() if duration_ms > 0 else 0,
                "range_end_ms": range_end,
            },
            "preview": {
                "name": player.get_track_display_name(
                    preview_item, getattr(player, "_preview_file_path", None)
                ),
                "track_id": get_item_track_id(preview_item) if preview_item is not None else None,
                "playlist_num": getattr(player, "_preview_playlist_num", None),
            },
            "selected": {
                "playlist_num": selected_num,
                "track_id": get_item_track_id(selected_item) if selected_item is not None else None,
                "row": (
                    selected_item.listWidget().row(selected_item)
                    if selected_item is not None and selected_item.listWidget() is not None
                    else None
                ),
            },
            "master_volume": float(player._playback_volume),
            "fade_mode": player._fade_mode,
            "fade_duration_ms": int(player.fade_duration_spin.value()),
            "fade_presets_ms": list(FADE_PRESETS_MS),
            "fade_preset_labels": [fade_preset_label(ms) for ms in FADE_PRESETS_MS],
            "high_cut_q": float(player._high_cut_q()),
            "high_cut_q_min": HIGH_CUT_Q_MIN,
            "high_cut_q_max": HIGH_CUT_Q_MAX,
            "high_cut_q_default": HIGH_CUT_Q_DEFAULT,
            "playback_mode": player.playback_mode,
            "advance": bool(player.space_advances_checkbox.isChecked()),
            "transitioning": bool(
                player._crossfade_active
                or player._high_cut_active
                or player._stop_fade_active
            ),
            "columns": self._serialize_columns(),
        }

    def _serialize_columns(self) -> list[dict]:
        player = self._player
        count = player.columns_spin.value() if hasattr(player, "columns_spin") else 0
        columns = getattr(player, "playlist_columns", None) or []
        result: list[dict] = []
        playing_item = player.current_playing_item
        selected_num, selected_item = player.get_selected_track()
        for col_index, column in enumerate(columns[:count]):
            tabs: list[dict] = []
            active_index = column.tabs.currentIndex()
            for tab_index in range(column.tabs.count()):
                page = column.tabs.widget(tab_index)
                playlist = getattr(page, "playlist_widget", None) if page else None
                if playlist is None:
                    continue
                title = column.tabs.tabText(tab_index).strip() or player.get_playlist_title(
                    playlist.playlist_num
                )
                tracks = []
                for row in range(playlist.count()):
                    item = playlist.item(row)
                    if item is None:
                        continue
                    track_id = get_item_track_id(item)
                    path = get_item_file_path(item)
                    tracks.append(
                        {
                            "id": track_id,
                            "row": row,
                            "name": player.get_track_display_name(item, path),
                            "duration_ms": get_item_duration(item),
                            "bpm": get_item_bpm(item),
                            "color": get_item_color(item),
                            "missing": is_item_file_missing(item),
                            "playing": item is playing_item,
                            "selected": item is selected_item
                            and playlist.playlist_num == selected_num,
                        }
                    )
                tabs.append(
                    {
                        "playlist_num": playlist.playlist_num,
                        "title": title,
                        "active": tab_index == active_index,
                        "tracks": tracks,
                    }
                )
            result.append({"index": col_index, "tabs": tabs, "active_tab": active_index})
        return result
