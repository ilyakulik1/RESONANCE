# Decibel Player

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/UI-PyQt6-41CD52.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

**Media server for live / broadcast use.**

Desktop app for audio and video playback

## Features

- **Air** player for program output, with sequential and crossfade transitions
- **Preview** and **Browser** for cueing, with separate audio output devices
- Up to 3 playlists — drag-and-drop, JSON import/export, duration and BPM
- Waveform timeline — play range, fade in/out, scrub
- Parametric EQ per track with live spectrum
- File browser with project and browse tabs
- Projects — save media, analysis, and mixer state
- Video mixer — scenes and layers, fullscreen or windowed output
- Formats: `.mp3`, `.wav`, `.m4a`, `.flac`, `.aac`

## Requirements

- **Python 3.10+**
- **ffmpeg** on your `PATH` (needed for audio decoding)
- Dependencies from [`requirements.txt`](requirements.txt)

## Installation

### 1. Install ffmpeg

**macOS (Homebrew):**

```bash
brew install ffmpeg
```

**Ubuntu / Debian:**

```bash
sudo apt update && sudo apt install ffmpeg
```

**Windows (Chocolatey):**

```bash
choco install ffmpeg
```

Check that it works:

```bash
ffmpeg -version
```

### 2. Clone the repository

```bash
git clone https://github.com/ilyakulik1/SimpleAudioPlayer.git
cd SimpleAudioPlayer
```

### 3. Create a virtual environment

```bash
python3 -m venv .venv
```

Activate it:

```bash
# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 4. Install Python packages

```bash
pip install -r requirements.txt
```

### 5. Run

```bash
python main.py
```

**Shortcuts:** `Space` — play/toggle · `Esc` — stop · `Ctrl+S` — save

## Standalone build

Build a packaged app with [PyInstaller](https://pyinstaller.org/) using [`MyAudioPlayer.spec`](MyAudioPlayer.spec):

```bash
pip install pyinstaller
pyinstaller MyAudioPlayer.spec
```

Output:

- **macOS:** `dist/MyAudioPlayer.app`
- **Windows / Linux:** `dist/MyAudioPlayer/`

The packaged binary is named `MyAudioPlayer`; the product name in the UI is **Decibel Player**.

## License

[GPL-3.0](LICENSE) · © 2026 Ilya Kulik
