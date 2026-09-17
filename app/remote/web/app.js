(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    conn: $("conn"),
    connLabel: $("conn-label"),
    now: $("now-playing"),
    airKicker: $("air-kicker"),
    airTitle: $("air-title"),
    previewLine: $("preview-line"),
    seek: $("seek"),
    timeElapsed: $("time-elapsed"),
    timeRemain: $("time-remain"),
    volume: $("volume"),
    volumeValue: $("volume-value"),
    fadeMode: $("fade-mode"),
    fadeMs: $("fade-ms"),
    fadeDown: $("fade-down"),
    fadeUp: $("fade-up"),
    fadeSlider: $("fade-slider"),
    fadePresets: $("fade-presets"),
    qRow: $("q-row"),
    highCutQ: $("high-cut-q"),
    qValue: $("q-value"),
    playbackMode: $("playback-mode"),
    advance: $("advance"),
    columnTabs: $("column-tabs"),
    playlistTabs: $("playlist-tabs"),
    tracks: $("tracks"),
    tabAdd: $("tab-add"),
    tabRename: $("tab-rename"),
    tabDelete: $("tab-delete"),
    btnPrev: $("btn-prev"),
    btnPlay: $("btn-play"),
    btnOnAir: $("btn-onair"),
    btnStop: $("btn-stop"),
    btnNext: $("btn-next"),
    sheet: $("sheet"),
    sheetTitle: $("sheet-title"),
    sheetBackdrop: $("sheet-backdrop"),
    sheetClose: $("sheet-close"),
  };

  let state = null;
  let activeColumn = 0;
  let draggingSeek = false;
  let draggingVolume = false;
  let draggingQ = false;
  let draggingFade = false;
  let playlistFp = "";
  let sheetTrack = null;
  let eventSource = null;
  let pollTimer = 0;
  let wakeLock = null;

  function formatTime(ms) {
    if (ms == null || ms < 0 || Number.isNaN(ms)) return "00:00";
    const total = Math.floor(ms / 1000);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  function formatBpm(bpm) {
    if (bpm == null) return "";
    const n = Number(bpm);
    if (!Number.isFinite(n) || n <= 0) return "";
    return String(Math.round(n));
  }

  async function command(action, params = {}) {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, params }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  function setConnected(online) {
    els.conn.classList.toggle("online", online);
    els.conn.classList.toggle("offline", !online);
    els.connLabel.textContent = online ? "live" : "offline";
  }

  function setSeg(root, value) {
    root.querySelectorAll("button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.mode === value);
    });
  }

  function currentColumn() {
    const columns = state?.columns || [];
    if (!columns.length) return null;
    if (activeColumn >= columns.length) activeColumn = 0;
    return columns[activeColumn];
  }

  function currentTab() {
    const column = currentColumn();
    if (!column) return null;
    return column.tabs.find((tab) => tab.active) || column.tabs[0] || null;
  }

  function applyState(next) {
    state = next;
    setConnected(true);
    const air = next.air || {};
    const live = Boolean(air.playing || next.transitioning);
    els.now.classList.toggle("live", live);
    document.body.classList.toggle("live", live);
    els.airKicker.textContent = next.transitioning ? "FADE" : live ? "ON AIR" : air.paused ? "PAUSED" : "AIR";
    els.airTitle.textContent = air.name || "No track";
    const previewName = next.preview?.name;
    els.previewLine.textContent = previewName ? `Preview — ${previewName}` : "Preview —";
    els.btnPlay.textContent = air.playing ? "❚❚" : "▶";

    if (!draggingSeek) {
      const start = air.range_start_ms || 0;
      const end = air.range_end_ms || air.duration_ms || 0;
      const span = Math.max(1, end - start);
      const pos = Math.max(start, Math.min(air.position_ms || 0, end));
      els.seek.max = String(span);
      els.seek.value = String(pos - start);
      els.timeElapsed.textContent = formatTime(pos - start);
      els.timeRemain.textContent = `−${formatTime(end - pos)}`;
    }

    if (!draggingVolume) {
      const pct = Math.round((next.master_volume || 0) * 100);
      els.volume.value = String(pct);
      els.volumeValue.textContent = `${pct}%`;
    }

    setSeg(els.fadeMode, next.fade_mode);
    els.fadeMs.textContent = String(next.fade_duration_ms ?? 0);
    if (!draggingFade && next.fade_duration_ms != null) {
      els.fadeSlider.value = String(next.fade_duration_ms);
    }
    renderPresets(next);
    els.qRow.classList.toggle("hidden", next.fade_mode !== "high_cut");
    if (!draggingQ && next.high_cut_q != null) {
      els.highCutQ.min = String(Math.round((next.high_cut_q_min || 0.5) * 100));
      els.highCutQ.max = String(Math.round((next.high_cut_q_max || 20) * 100));
      els.highCutQ.value = String(Math.round(next.high_cut_q * 100));
      els.qValue.textContent = Number(next.high_cut_q).toFixed(2);
    }
    setSeg(els.playbackMode, next.playback_mode);
    els.advance.checked = Boolean(next.advance);

    const fp = JSON.stringify({
      columns: next.columns,
      selected: next.selected,
      playing: air.track_id,
    });
    if (fp !== playlistFp) {
      playlistFp = fp;
      renderPlaylists(next);
    }
  }

  function renderPresets(next) {
    const presets = next.fade_presets_ms || [];
    const labels = next.fade_preset_labels || [];
    const current = next.fade_duration_ms;
    els.fadePresets.replaceChildren(
      ...presets.map((ms, i) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = labels[i] || String(ms);
        btn.classList.toggle("active", ms === current);
        btn.addEventListener("click", () => command("fade_duration", { ms }));
        return btn;
      })
    );
  }

  function renderPlaylists(next) {
    const columns = next.columns || [];
    if (activeColumn >= columns.length) activeColumn = 0;

    els.columnTabs.replaceChildren();
    els.columnTabs.classList.toggle("hidden", columns.length <= 1);
    columns.forEach((col, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = `COL ${index + 1}`;
      btn.classList.toggle("active", index === activeColumn);
      btn.addEventListener("click", () => {
        activeColumn = index;
        playlistFp = "";
        renderPlaylists(state);
      });
      els.columnTabs.appendChild(btn);
    });

    const column = currentColumn();
    els.playlistTabs.replaceChildren();
    if (!column) {
      els.tracks.replaceChildren();
      return;
    }
    column.tabs.forEach((tab) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = tab.title;
      btn.classList.toggle("active", Boolean(tab.active));
      btn.addEventListener("click", () => {
        command("activate_tab", { playlist_num: tab.playlist_num });
      });
      els.playlistTabs.appendChild(btn);
    });

    const tab = currentTab();
    els.tracks.replaceChildren();
    if (!tab || !tab.tracks.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "Playlist is empty";
      els.tracks.appendChild(empty);
      return;
    }
    tab.tracks.forEach((track) => {
      const li = document.createElement("li");
      li.className = "track";
      if (track.selected) li.classList.add("selected");
      if (track.playing) li.classList.add("playing");
      if (track.missing) li.classList.add("missing");

      const mark = document.createElement("span");
      mark.className = "mark";
      if (track.color) mark.style.background = track.color;
      else if (track.playing) mark.style.background = "#e45656";

      const name = document.createElement("div");
      name.className = "name";
      name.textContent = track.name;

      const meta = document.createElement("div");
      meta.className = "meta";
      const bits = [formatTime(track.duration_ms)];
      const bpm = formatBpm(track.bpm);
      if (bpm) bits.push(`${bpm} BPM`);
      meta.textContent = bits.join(" · ");

      const more = document.createElement("button");
      more.type = "button";
      more.className = "more";
      more.setAttribute("aria-label", "Track actions");
      more.textContent = "⋮";
      more.addEventListener("click", (event) => {
        event.stopPropagation();
        openSheet(tab.playlist_num, track);
      });

      li.append(mark, name, meta, more);
      li.addEventListener("click", () => {
        command("select_track", { playlist_num: tab.playlist_num, track_id: track.id, row: track.row });
      });
      li.addEventListener("dblclick", () => {
        command("play_track", { playlist_num: tab.playlist_num, track_id: track.id, row: track.row });
      });
      els.tracks.appendChild(li);
    });
  }

  function openSheet(playlistNum, track) {
    sheetTrack = { playlist_num: playlistNum, track };
    els.sheetTitle.textContent = track.name;
    els.sheet.hidden = false;
    els.sheet.classList.remove("hidden");
  }

  function closeSheet() {
    sheetTrack = null;
    els.sheet.hidden = true;
    els.sheet.classList.add("hidden");
  }

  async function onSheetAction(act) {
    if (!sheetTrack) return;
    const { playlist_num, track } = sheetTrack;
    const ident = { playlist_num, track_id: track.id, row: track.row };
    closeSheet();
    if (act === "play") await command("play_track", ident);
    else if (act === "select") await command("select_track", ident);
    else if (act === "up") await command("move_track", { ...ident, where: "up" });
    else if (act === "down") await command("move_track", { ...ident, where: "down" });
    else if (act === "top") await command("move_track", { ...ident, where: "top" });
    else if (act === "bottom") await command("move_track", { ...ident, where: "bottom" });
    else if (act === "remove") await command("remove_track", ident);
  }

  function bindSeg(root, action, extra = {}) {
    root.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        command(action, { ...extra, mode: btn.dataset.mode });
      });
    });
  }

  function startSSE() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    const es = new EventSource("/api/events");
    eventSource = es;
    es.onmessage = (event) => {
      try {
        applyState(JSON.parse(event.data));
      } catch {
        /* ignore malformed frames */
      }
    };
    es.onerror = () => {
      setConnected(false);
      if (es.readyState === EventSource.CLOSED) {
        eventSource = null;
        startPoll();
        window.setTimeout(startSSE, 2000);
      }
    };
  }

  function startPoll() {
    if (pollTimer) return;
    pollTimer = window.setInterval(async () => {
      if (eventSource && eventSource.readyState === EventSource.OPEN) {
        window.clearInterval(pollTimer);
        pollTimer = 0;
        return;
      }
      try {
        const res = await fetch("/api/state", { cache: "no-store" });
        applyState(await res.json());
        if (eventSource) {
          window.clearInterval(pollTimer);
          pollTimer = 0;
        }
      } catch {
        setConnected(false);
      }
    }, 700);
  }

  async function requestWakeLock() {
    try {
      if (navigator.wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
      }
    } catch {
      wakeLock = null;
    }
  }

  bindSeg(els.fadeMode, "fade_mode");
  bindSeg(els.playbackMode, "playback_mode");

  els.fadeDown.addEventListener("click", () => command("fade_preset_step", { direction: -1 }));
  els.fadeUp.addEventListener("click", () => command("fade_preset_step", { direction: 1 }));
  els.fadeSlider.addEventListener("pointerdown", () => { draggingFade = true; });
  els.fadeSlider.addEventListener("input", () => {
    els.fadeMs.textContent = els.fadeSlider.value;
  });
  els.fadeSlider.addEventListener("change", () => {
    draggingFade = false;
    command("fade_duration", { ms: Number(els.fadeSlider.value) });
  });
  els.advance.addEventListener("change", () => {
    command("advance_enabled", { enabled: els.advance.checked });
  });

  els.seek.addEventListener("pointerdown", () => { draggingSeek = true; });
  els.seek.addEventListener("pointerup", () => { draggingSeek = false; });
  els.seek.addEventListener("change", () => {
    const air = state?.air || {};
    const start = air.range_start_ms || 0;
    command("seek", { position_ms: start + Number(els.seek.value) });
    draggingSeek = false;
  });

  let volumeTimer = 0;
  els.volume.addEventListener("pointerdown", () => { draggingVolume = true; });
  els.volume.addEventListener("input", () => {
    els.volumeValue.textContent = `${els.volume.value}%`;
    window.clearTimeout(volumeTimer);
    volumeTimer = window.setTimeout(() => {
      command("volume", { volume: Number(els.volume.value) / 100 });
    }, 80);
  });
  els.volume.addEventListener("change", () => {
    draggingVolume = false;
    command("volume", { volume: Number(els.volume.value) / 100 });
  });

  els.highCutQ.addEventListener("pointerdown", () => { draggingQ = true; });
  els.highCutQ.addEventListener("input", () => {
    els.qValue.textContent = (Number(els.highCutQ.value) / 100).toFixed(2);
  });
  els.highCutQ.addEventListener("change", () => {
    draggingQ = false;
    command("high_cut_q", { q: Number(els.highCutQ.value) / 100 });
  });

  els.btnOnAir.addEventListener("click", () => {
    if (navigator.vibrate) navigator.vibrate(20);
    command("on_air");
  });
  els.btnStop.addEventListener("click", () => command("stop"));
  els.btnPlay.addEventListener("click", () => command("play_pause"));
  els.btnPrev.addEventListener("click", () => command("previous"));
  els.btnNext.addEventListener("click", () => command("next"));

  els.tabAdd.addEventListener("click", () => {
    command("add_playlist", { column: activeColumn });
  });
  els.tabRename.addEventListener("click", async () => {
    const tab = currentTab();
    if (!tab) return;
    const title = window.prompt("Playlist name", tab.title);
    if (title == null) return;
    const trimmed = title.trim();
    if (!trimmed) return;
    await command("rename_playlist", { playlist_num: tab.playlist_num, title: trimmed });
  });
  els.tabDelete.addEventListener("click", async () => {
    const tab = currentTab();
    if (!tab) return;
    if (!window.confirm(`Delete “${tab.title}”?`)) return;
    await command("delete_playlist", { playlist_num: tab.playlist_num });
  });

  els.sheet.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.addEventListener("click", () => onSheetAction(btn.dataset.act));
  });
  els.sheetBackdrop.addEventListener("click", closeSheet);
  els.sheetClose.addEventListener("click", closeSheet);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") requestWakeLock();
  });

  startSSE();
  startPoll();
  requestWakeLock();
})();
